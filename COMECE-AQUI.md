# Instalar o Traslado

Baixe o ZIP em [Releases do Traslado](https://github.com/rickfreoa/traslado/releases), extraia todos os arquivos para uma pasta e escolha o passo do seu sistema. Mantenha a conexão com a internet durante a instalação. O instalador prepara o Python e as bibliotecas na sua conta, sem pedir senha de administrador.

| Sistema | Como instalar | Como abrir depois |
|---|---|---|
| Windows 10/11 x64 | Abra **INSTALAR.bat** na pasta extraída. | Abra **Traslado** na Área de Trabalho ou no Menu Iniciar. |
| macOS Apple Silicon ou Intel | Abra **instalar.command** na pasta extraída. Se o macOS bloquear o script, use a opção **Abrir Mesmo Assim** em **Ajustes do Sistema → Privacidade e Segurança** somente para o arquivo da release oficial. | Abra **Traslado** na pasta **Aplicativos** de sua conta (`~/Applications`). |
| Linux x86-64 ou ARM64 com glibc | Abra um terminal na pasta extraída e execute `sh instalar.sh`. | Procure **Traslado** no menu de aplicativos do ambiente gráfico. |

Se o Finder indicar que o arquivo perdeu a permissão de execução, entre na pasta extraída pelo Terminal e execute `sh instalar.command`. No Linux são necessários `sh`, `curl` ou `wget`, `tar`, `gzip` e certificados raiz, normalmente presentes em instalações desktop. A interface precisa de uma sessão gráfica.

Depois de abrir, escolha um arquivo, selecione a pasta de saída e clique em **Converter**. O instalador de Windows inclui OCR em seis idiomas. No Linux e no macOS, o Traslado usa um Tesseract compatível que já esteja disponível, com modelos de idioma preparados na conta. Com a instalação padrão sem Tesseract, continuam disponíveis PDFs com texto, Word, Excel e os demais formatos nativos. PDFs escaneados e imagens que dependem de OCR precisam do motor.

Para atualizar, baixe o ZIP da nova release e repita o passo do seu sistema. Os documentos e resultados permanecem na sua pasta de dados.

Para conferir o pacote antes de executar, compare o SHA-256 com o arquivo `SHA256SUMS` da release. Com o GitHub CLI, a atestação dos artefatos pode ser conferida com `gh attestation verify <arquivo> --repo rickfreoa/traslado`. O atestado é de proveniência do GitHub. Windows e macOS podem mostrar alertas por ausência de assinatura Authenticode ou notarização Apple.
