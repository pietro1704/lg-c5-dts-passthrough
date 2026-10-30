# Wiki: LG C5 DTS / DTS-HD MA passthrough

Integração experimental de passthrough para players nativos e HDMI na LG C5.
Preserva o bitstream DTS; não decodifica, não converte e não faz downmix.

- [Instalação, atualização e desativação](Installation.md)
- [Arquitetura e preparação de pacotes](Architecture.md)
- [Testes, aceitação e limites](Testing.md)
- [Diagnóstico e recuperação](Troubleshooting.md)

A [v0.3.0](https://github.com/pietro1704/lg-c5-dts-passthrough/releases/tag/v0.3.0)
é uma pré-release **somente de código-fonte**. Não há instalador binário global
público nesta versão. O arquivo de fontes não pode ser instalado diretamente na
TV. O binário nativo da v0.2.0 não inclui as correções da v0.3.0.

Estas páginas são mantidas em `docs/wiki` no repositório para revisão por PR.
A aceitação registrada se refere aos artefatos testados em 30/10/2026; recompilar
ou mudar firmware, bibliotecas, receiver ou fonte exige nova validação.
