# Wiki: LG C5 DTS / DTS-HD MA passthrough

Integração experimental de passthrough para players nativos e HDMI na LG C5.
Preserva o bitstream DTS; não decodifica, não converte e não faz downmix.

- [Instalação, atualização e desativação](Installation.md)
- [Arquitetura e preparação de pacotes](Architecture.md)
- [Testes, aceitação e limites](Testing.md)
- [Diagnóstico e recuperação](Troubleshooting.md)

A [v0.4.0](https://github.com/pietro1704/lg-c5-dts-passthrough/releases/tag/v0.4.0)
traz o pacote **-tv.tar.gz** com `install.sh` e `remove.sh`. Use esse arquivo
para instalar; “Source code” é destinado a desenvolvimento. A v0.3.0 era apenas
de fontes e o binário da v0.2.0 não contém as correções atuais.

Estas páginas são mantidas em `docs/wiki` no repositório para revisão por PR.
A aceitação registrada se refere aos artefatos testados em 30/10/2026; recompilar
ou mudar firmware, bibliotecas, receiver ou fonte exige nova validação.
