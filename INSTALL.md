# Instalar na LG C5

**Somente LG C5, firmware 33.31.68 e kernel `5.4.268-329.ptl4tv.5`.**
A TV já precisa ter root, Homebrew, Python 3 e `dts_restore-2.0` compatível em
`/home/root/dts_restore-2.0`. O receiver precisa suportar DTS por eARC.
HDMI foi validado com PS5 na HDMI3. Este pacote não instala esses pré-requisitos.

## Instalação em 3 passos

1. Baixe **`lg-c5-dts-passthrough-v0.4.0-tv.tar.gz`** e `SHA256SUMS` na
   [release v0.4.0](https://github.com/pietro1704/lg-c5-dts-passthrough/releases/tag/v0.4.0).
   **Não escolha “Source code”.** Confira o checksum do arquivo no computador
   (`sha256sum --ignore-missing -c SHA256SUMS` no Linux; no Mac compare
   `shasum -a 256 lg-c5-dts-passthrough-v0.4.0-tv.tar.gz` com `SHA256SUMS`).
2. Copie o arquivo para a TV pelo seu acesso SSH/SFTP já configurado e entre
   como **root** na pasta em que o salvou. Execute:

   ```sh
   tar -xzf lg-c5-dts-passthrough-v0.4.0-tv.tar.gz
   cd lg-c5-dts-passthrough-v0.4.0
   sh install.sh
   ```

3. Aguarde **“Instalação concluída”** e reinicie a TV manualmente. Depois,
   abra seu player e teste uma faixa DTS. Para Blu-ray na PS5, selecione
   **Bitstream** no player do disco; jogos usam a preferência de áudio da PS5.

Opcional: `sh install.sh --check` confere o pacote sem instalar nada.
Não confirma o som, o firmware nem o receiver. Confira modelo/firmware nas
informações da TV e o kernel com `uname -r`.

## Deu erro ou quer remover?

Na mesma pasta extraída, como root:

```sh
sh remove.sh
```

Aguarde **“Integração desativada”** e reinicie a TV manualmente.
Isso retira esta integração do boot e restaura seu registro GStreamer anterior.
**Não remove o dts_restore nem as versões guardadas e não faz reset de fábrica.**
O backup temporário do registro é consumido depois de restaurado com sucesso.
Módulos em uso ficam residentes até reiniciar; os scripts não interrompem o áudio.

Se uma instalação nova falhar, o script tenta desfazer o boot criado. Se a
recuperação também falhar, ele avisa e preserva os arquivos: não reinicie ainda
nem apague o estado manualmente. Guarde o erro e consulte
[diagnóstico](https://github.com/pietro1704/lg-c5-dts-passthrough/wiki/Troubleshooting).

## Já está funcionando?

Não precisa reinstalar. Uma instalação diferente é preservada e recusada, sem
upgrade automático. Não remova a instalação atual apenas para experimentar.
Guarde a pasta extraída: ela contém o script de remoção.

Esta é uma pré-release experimental. Reutiliza os binários previamente aceitos;
os novos scripts são testados em CI com arquivos temporários e hardware simulado.
Não foram testados novamente em TV real. Outros firmwares/modelos não são suportados.
