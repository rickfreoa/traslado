"""User-visible contracts: replacement recovery, OCR languages and exact chunk slices."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from freoa_v6 import chunks, cli, gui, ocr, runner
from freoa_v6.model import Block, Document, Options, Page
from freoa_v6.render import render


class CorrectionsRC9(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "Doutrina.txt"
        self.source.write_text("Primeira versão " * 80, encoding="utf-8")
        self.out = self.root / "out"

    def convert(self, **kwargs):
        return runner.execute(self.source, self.out, Options(**kwargs))

    def initial(self):
        result = self.convert(chunks=True)
        self.assertEqual(result['status'], 'complete', result)
        return Path(result['output'])

    def contents(self, target):
        return {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()}

    def test_overwrite_replaces_same_folder_and_removes_stale_chunks(self):
        target = self.initial()
        self.source.write_text("Nova versão", encoding='utf-8')
        result = self.convert(overwrite=True)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['output'], str(target))
        self.assertTrue(result['overwritten'])
        self.assertIn('Nova versão', (target/'document.md').read_text())
        self.assertFalse((target/'chunks').exists())
        self.assertEqual(list(self.out.iterdir()), [target])

    def test_unchecked_keeps_numbered_copies(self):
        target = self.initial()
        before = self.contents(target)
        result = self.convert()
        self.assertEqual(result['status'], 'complete', result)
        self.assertFalse(result['overwritten'])
        self.assertNotEqual(result['output'], str(target))
        self.assertEqual(self.contents(target), before)

    def test_overwrite_first_run_works(self):
        result = self.convert(overwrite=True)
        self.assertEqual(result['status'], 'complete', result)
        self.assertFalse(result['overwritten'])

    def test_rc8_manifest_without_source_path_is_supported(self):
        target = self.initial()
        path = target/'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['converter'] = '6.0.0rc8'
        manifest.pop('source_path')
        manifest.pop('directories')
        path.write_text(json.dumps(manifest))
        self.source.write_text('updated')
        self.assertEqual(self.convert(overwrite=True)['status'], 'complete')

    def test_unknown_directory_is_preserved(self):
        target = self.out/'Doutrina'
        target.mkdir(parents=True)
        (target/'manual.txt').write_text('preserve')
        before = self.contents(target)
        result = self.convert(overwrite=True)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('Sobrescrita recusada', result['error'])
        self.assertEqual(self.contents(target), before)

    def test_edited_output_is_preserved(self):
        target = self.initial()
        (target/'document.md').write_text('User revision')
        before = self.contents(target)
        self.assertEqual(self.convert(overwrite=True)['status'], 'failed')
        self.assertEqual(self.contents(target), before)

    def test_added_file_and_empty_directory_are_preserved(self):
        target = self.initial()
        for kind in ('file', 'directory'):
            with self.subTest(kind=kind):
                added = target/'notes'
                added.write_text('User notes') if kind == 'file' else added.mkdir()
                self.assertEqual(self.convert(overwrite=True)['status'], 'failed')
                self.assertTrue(added.exists())
                added.unlink() if kind == 'file' else added.rmdir()

    def test_same_filename_from_another_folder_is_not_replaced(self):
        target = self.initial()
        before = self.contents(target)
        other = self.root/'another'/'Doutrina.txt'
        other.parent.mkdir()
        other.write_text('other source')
        result = runner.execute(other, self.out, Options(overwrite=True))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.contents(target), before)

    def test_invalid_manifest_path_is_refused(self):
        target = self.initial()
        path = target/'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['files'][0]['path'] = '../outside.txt'
        path.write_text(json.dumps(manifest))
        self.assertEqual(self.convert(overwrite=True)['status'], 'failed')
        self.assertTrue(target.exists())

    def test_worker_failure_keeps_previous_output(self):
        target = self.initial()
        before = self.contents(target)
        result = self.convert(overwrite=True, max_output_bytes=100)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(self.contents(target), before)

    def test_cancel_keeps_previous_output(self):
        target = self.initial()
        before = self.contents(target)
        result = runner.execute(self.source, self.out, Options(overwrite=True), cancel=lambda: True)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(self.contents(target), before)

    def test_publication_failure_restores_previous_output(self):
        target = self.initial()
        before = self.contents(target)
        rename = Path.rename
        def fail_new(path, destination):
            if path.name == 'package':
                raise OSError('Injected publication failure')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail_new):
            result = self.convert(overwrite=True)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(self.contents(target), before)
        self.assertEqual(list(self.out.iterdir()), [target])

    def test_cancel_during_previous_verification_prevents_replacement(self):
        target=self.initial()
        before=self.contents(target)
        cancelled=[False]
        verify=runner._verify_previous
        def cancel_after_verification(*args,**kwargs):
            result=verify(*args,**kwargs)
            cancelled[0]=True
            return result
        with patch.object(runner,'_verify_previous',cancel_after_verification):
            result=runner.execute(self.source,self.out,Options(overwrite=True),cancel=lambda:cancelled[0])
        self.assertEqual(result['status'],'cancelled',result)
        self.assertEqual(self.contents(target),before)
        self.assertEqual(list(self.out.iterdir()),[target])

    def test_failed_recovery_preserves_backup_outside_cleanup(self):
        target = self.initial()
        before = self.contents(target)
        rename = Path.rename
        def fail(path, destination):
            if path.name == 'package' or path.name.startswith('.freoa-anterior-'):
                raise OSError('Injected failure')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail):
            result = self.convert(overwrite=True)
        self.assertEqual(result['status'], 'failed', result)
        backups = list(self.out.glob('.freoa-anterior-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.contents(backups[0]), before)
        self.assertIn(str(backups[0]), result['error'])

    def test_busy_publication_reservation_is_not_removed(self):
        target = self.initial()
        before = self.contents(target)
        lock = self.out/'.freoa-reserva-Doutrina'
        lock.write_text('another process')
        self.assertEqual(self.convert(overwrite=True)['status'], 'failed')
        self.assertTrue(lock.exists())
        self.assertEqual(self.contents(target), before)

    def test_change_in_backup_prevents_its_deletion(self):
        target = self.initial()
        rename = Path.rename
        def add_note_after_publication(path, destination):
            result = rename(path, destination)
            if path.name == 'package':
                backups = list(self.out.glob('.freoa-anterior-*'))
                (backups[0]/'manual.txt').write_text('retain this note')
            return result
        with patch.object(Path, 'rename', add_note_after_publication):
            result = self.convert(overwrite=True)
        self.assertEqual(result['status'], 'complete', result)
        self.assertIn('cleanup_warning',result)
        backups = list(self.out.glob('.freoa-anterior-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual((backups[0]/'manual.txt').read_text(),'retain this note')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink fixture')
    def test_symlink_target_is_refused(self):
        self.out.mkdir()
        target = self.out/'Doutrina'
        target.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.convert(overwrite=True)['status'], 'failed')
        self.assertTrue(target.is_symlink())

    def test_overwrite_requires_boolean(self):
        with self.assertRaises(ValueError):
            Options(overwrite=1)

    def test_gui_options_use_checkbox_value_and_config_selection(self):
        app = gui.App.__new__(gui.App)
        values = {'config_path':'', 'mode':'auto', 'language':'eng', 'chunk':False,
                  'chunk_size':'6000', 'chunk_overlap':'600', 'scan_previews':False, 'overwrite':True}
        for name, value in values.items():
            setattr(app, name, SimpleNamespace(get=lambda value=value: value, set=lambda value, name=name: setattr(app, name, SimpleNamespace(get=lambda: value))))
        self.assertTrue(app.conversion_options().overwrite)
        config = self.root/'options.toml'
        config.write_text('[conversion]\noverwrite = false\n')
        with patch.object(gui.filedialog, 'askopenfilename', return_value=str(config)):
            app.choose_config()
        self.assertFalse(app.conversion_options().overwrite)

    def test_cli_flags_override_toml(self):
        config = self.root/'options.toml'
        config.write_text('[conversion]\noverwrite = true\n')
        seen = []
        def capture(source, output, options):
            seen.append(options.overwrite)
            return {'status':'complete'}
        for flag in ('--overwrite', '--no-overwrite'):
            with patch.object(sys, 'argv', ['freoa-md','convert',str(self.source),'-o',str(self.out),'--config',str(config),flag]), patch.object(cli, 'execute', capture):
                self.assertEqual(cli.main(), 0)
        self.assertEqual(seen, [True, False])

    def test_chunk_starts_on_word_and_exact_offsets_preserve_coverage(self):
        text = '# Prosa\n\n' + '\n\n'.join('ação palavra final. ' * n for n in [70,180,55,200]*8)
        document = Document('Prosa.md','a'*64,[Page(1,'native',[Block('text',text)])])
        text = render(document)
        records = chunks.generate_chunks(document, text, size_chars=6000, overlap_chars=600)
        for previous, current in zip(records, records[1:]):
            start, end = current['char_start'], current['char_end']
            self.assertFalse(text[start].isspace())
            self.assertTrue(text[start-1].isspace())
            self.assertEqual(current['text'], text[start:end])
            self.assertLessEqual(start, previous['char_end'])
            self.assertLessEqual(current['actual_overlap_chars'], 600)
        self.assertEqual(records[0]['char_start'], 0)
        self.assertEqual(records[-1]['char_end'], len(text))

    def test_zero_overlap_never_skips_whitespace(self):
        text = ('word '*80 + '\n'*300)*4
        ranges = chunks._ranges(text,256,0,[])
        self.assertEqual(''.join(text[a:b] for a,b in ranges), text)

    def test_long_whitespace_and_atoms_keep_exact_coverage(self):
        text = 'word '*100 + '\n'*1000 + '`atomic code`' + ' end '*100
        atoms = [(text.index('`'),text.rindex('`')+1)]
        ranges = chunks._ranges(text,256,200,atoms)
        self.assertEqual(ranges[0][0],0)
        self.assertEqual(ranges[-1][1],len(text))
        for previous, current in zip(ranges,ranges[1:]):
            self.assertLessEqual(current[0],previous[1])
            self.assertGreater(current[1],previous[1])
            self.assertFalse(any(a<current[0]<b or a<current[1]<b for a,b in atoms))

    def ocr_mock(self, available='eng', diagnostics='', code=0):
        engine = self.root/'tesseract.exe'
        engine.write_bytes(b'inert')
        self.calls = []
        def bounded(argv,cwd,timeout,log):
            self.calls.append(argv)
            if '--list-langs' in argv:
                log.write_text('List of available languages:\n' + available + '\n')
                return 0
            base = Path(argv[2])
            base.with_suffix('.txt').write_text('OCR_SENTINEL')
            base.with_suffix('.tsv').write_text('conf\ttext\n99\tOCR_SENTINEL\n')
            log.write_text(diagnostics)
            return code
        return engine, bounded

    def test_missing_language_fails_before_recognition(self):
        engine, bounded = self.ocr_mock()
        with patch.object(ocr,'run_bounded',bounded):
            with self.assertRaisesRegex(RuntimeError, 'Idiomas OCR ausentes: fra, por'):
                ocr.recognize(self.root/'image.png',self.root,Options(tesseract=str(engine),language='eng+fra+por'))
        self.assertEqual(len(self.calls),1)

    def test_load_error_with_zero_exit_is_not_silent_success(self):
        engine, bounded = self.ocr_mock('eng\nfra', "Error opening data file fra.traineddata\nFailed loading language 'fra'", 0)
        with patch.object(ocr,'run_bounded',bounded):
            with self.assertRaisesRegex(RuntimeError, 'idiomas solicitados'):
                ocr.recognize(self.root/'image.png',self.root,Options(tesseract=str(engine),language='eng+fra'))

    def test_success_records_requested_and_available_languages(self):
        engine, bounded = self.ocr_mock('eng\nfra')
        with patch.object(ocr,'run_bounded',bounded):
            block, warnings = ocr.recognize(self.root/'image.png',self.root,Options(tesseract=str(engine),language='eng'))
        self.assertEqual(block.details['requested_languages'],['eng'])
        self.assertEqual(block.details['available_languages'],['eng','fra'])
        self.assertEqual(block.details['language_validation'],'listed_and_no_load_error')
        self.assertEqual(block.details['confidence_kind'],'mean_word_score_not_accuracy')
        self.assertEqual(warnings,[])

    def test_preflight_uses_same_explicit_tessdata(self):
        engine, bounded = self.ocr_mock()
        data = self.root/'tessdata';data.mkdir()
        with patch.object(ocr,'run_bounded',bounded):
            ocr.recognize(self.root/'image.png',self.root,Options(tesseract=str(engine),tessdata=str(data),language='eng'))
        for argv in self.calls:
            self.assertEqual(argv[argv.index('--tessdata-dir')+1],str(data))

    def test_mixed_pdf_keeps_native_page_and_reports_missing_ocr_language(self):
        import fitz
        from PIL import Image
        source = self.root/'mixed.pdf'
        image = self.root/'scan.png'
        Image.new('RGB',(600,800),'gray').save(image)
        pdf = fitz.open()
        pdf.new_page().insert_text((30,50),'NATIVE_SOURCE_SENTINEL preserved')
        page = pdf.new_page()
        page.insert_image(page.rect,filename=str(image))
        pdf.save(source);pdf.close()
        data=self.root/'empty-tessdata';data.mkdir()
        engine=shutil.which('tesseract')
        if not engine:
            self.skipTest('Tesseract unavailable')
        result=runner.execute(source,self.out,Options(tesseract=engine,tessdata=str(data),language='eng'))
        self.assertEqual(result['status'],'partial',result)
        output=Path(result['output'])
        doc=json.loads((output/'document.json').read_text())
        self.assertEqual(doc['fidelity'],'unverified')
        self.assertIn('NATIVE_SOURCE_SENTINEL',(output/'document.md').read_text())
        self.assertTrue(any(w.startswith('ocr_failed:') and 'Idiomas OCR ausentes' in w for w in doc['pages'][1]['warnings']))

    @unittest.skipUnless(shutil.which('tesseract') and Path('/usr/share/tesseract-ocr/5/tessdata/eng.traineddata').is_file(), 'Linux Tesseract/eng fixture unavailable')
    def test_real_tesseract_missing_and_corrupt_models(self):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB',(1500,180),'white')
        ImageDraw.Draw(image).text((20,30),'THE QUICK BROWN FOX 12345',font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',44),fill='black')
        path=self.root/'sample.png';image.save(path)
        data=self.root/'tessdata';data.mkdir()
        shutil.copyfile('/usr/share/tesseract-ocr/5/tessdata/eng.traineddata',data/'eng.traineddata')
        base={'tesseract':shutil.which('tesseract'),'tessdata':str(data)}
        with self.assertRaisesRegex(RuntimeError,'Idiomas OCR ausentes'):
            ocr.recognize(path,self.root,Options(language='eng+fra+por',**base))
        (data/'fra.traineddata').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError,'idiomas solicitados'):
            ocr.recognize(path,self.root,Options(language='eng+fra',**base))
        block,_=ocr.recognize(path,self.root,Options(language='eng',**base))
        self.assertIn('QUICK BROWN FOX',block.text)


if __name__ == '__main__':
    unittest.main()
