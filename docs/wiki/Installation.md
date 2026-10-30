# Instalar e remover na TV

O [guia curto](https://github.com/pietro1704/lg-c5-dts-passthrough/blob/main/INSTALL.md)
contém os pré-requisitos e os três passos de instalação.

1. Baixe **lg-c5-dts-passthrough-v0.4.0-tv.tar.gz** na
   [release v0.4.0](https://github.com/pietro1704/lg-c5-dts-passthrough/releases/tag/v0.4.0).
   Confira `SHA256SUMS`. Não escolha “Source code”.
2. Copie o arquivo para a TV e, pelo terminal SSH como root, execute na pasta do arquivo:

   ```sh
   tar -xzf lg-c5-dts-passthrough-v0.4.0-tv.tar.gz
   cd lg-c5-dts-passthrough-v0.4.0
   sh install.sh
   ```

3. Após “Instalação concluída”, reinicie a TV manualmente.

**Para desfazer:** na mesma pasta, execute `sh remove.sh`; após “Integração
desativada”, reinicie manualmente. O script restaura o registro GStreamer anterior,
desativa esta integração e preserva o dts_restore e as versões guardadas.
O backup temporário do registro é consumido após a restauração. Não faz reset de fábrica.

## Antes de instalar

Somente **LG C5 / firmware 33.31.68 / kernel 5.4.268-329.ptl4tv.5**.
A TV já precisa ter root, Homebrew, Python 3 e `dts_restore-2.0` compatível em
`/home/root/dts_restore-2.0`. Receiver com DTS via eARC; para MA, suporte MA.
PS5 na **HDMI3 da interface / porta1 do driver**. Modelo e firmware devem ser
conferidos nas informações da TV; `uname -r` informa o kernel.
Os scripts não instalam pré-requisitos e não habilitam root.

`sh install.sh --check` só confere o pacote; não comprova compatibilidade ou som.
`sh remove.sh --check`, no alvo, identifica a instalação sem remover.
Guarde a pasta extraída para usar a remoção. Se já está funcionando, não precisa
reinstalar. Esta release não foi ensaiada novamente na TV real do mantenedor.

## Como os scripts protegem a instalação

O instalador confere hashes e arquitetura antes de preparar o boot. Ele usa o
instalador existente para criar versões imutáveis e preparar o registro do
GStreamer com journal. Não reinicia a TV nem inicia controladores na sessão atual.
Uma falha em instalação nova tenta desfazer apenas o boot recém-criado e restaurar
o registro. Falha ao repetir uma instalação existente não a remove automaticamente.

A remoção confere identidade e backup antes de restaurar. Mudanças de terceiros,
estado desconhecido ou backup sem journal interrompem a operação, preservando os
arquivos. Em erro de recuperação, não reinicie ainda nem apague o estado manualmente;
consulte [diagnóstico](Troubleshooting.md).

## Atualização e migração

Repetir o mesmo pacote é permitido. Uma versão diferente ou o hook nativo antigo
é recusado, sem substituição automática. O pacote atual continua preservado.
Para uma atualização planejada, confira primeiro o novo pacote, remova a
integração antiga com o script dela e só então instale a nova, antes de reboot.
Não inicie controladores manualmente nem force unload de módulos residentes.

Para instalações anteriores sem `remove.sh`, a desativação global existente é:

```sh
python3 /home/root/c5-dts-global/current/global_install.py disable-boot
```

O pacote nativo antigo tem outro instalador:

```sh
python3 /home/root/c5-dts-passthrough/current/installer.py disable-boot
```

Use apenas o comando correspondente à instalação que você identificou.
Não faça migração se não precisa alterar algo que já funciona.
