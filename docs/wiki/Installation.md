# Instalação

## Compatibilidade e conteúdo disponível

Alvo validado: LG C5 com firmware **33.31.68**, kernel
`5.4.268-329.ptl4tv.5`, webOS com root, Homebrew e uma instalação compatível de
`dts_restore-2.0` em `/home/root/dts_restore-2.0`. São necessários Python 3,
bibliotecas GStreamer/FFmpeg compatíveis e receiver eARC que anuncie DTS;
DTS-HD MA requer capacidade MA de oito canais/192 kHz no transporte.
A integração HDMI atual é específica da entrada **HDMI3 na interface / porta1
no driver**, utilizada com PS5. Não presuma suporte a outra porta ou modelo.

Confirme modelo e firmware nas informações da TV; confira o kernel com
`uname -r`. O instalador verifica kernel e integridade, mas isso não substitui
conferir o firmware, as bibliotecas privadas e o equipamento conectado.
Mantenha acesso root de recuperação e o pacote anterior antes de alterar o boot.

A v0.3.0 contém apenas fontes. Para conferir o download, coloque o arquivo
`lg-c5-dts-passthrough-v0.3.0-source.tar.gz` e `SHA256SUMS` da mesma release
em uma pasta e execute:

```sh
sha256sum -c SHA256SUMS
# macOS: shasum -a 256 -c SHA256SUMS
```

O checksum confere o download; não transforma fontes em pacote instalável.
Para produzir um candidato com artefatos próprios e evidência do alvo, consulte
[preparação de pacotes](Architecture.md). Firmware, toolchain, bibliotecas,
modelos de configuração de aparelhos e mídia não são distribuídos.

## Instalar um pacote global verificado

Este procedimento exige um pacote global previamente construído e validado,
com `global_install.py`, `global-manifest.json`, `native/`, `hdmi/` e `hdmi_hd/`.
Não execute o instalador diretamente da árvore de fontes: faltam esses artefatos.
Transfira o pacote por seu acesso root existente e extraia em uma pasta privada.
Na raiz desse pacote:

```sh
python3 global_install.py dry-run
```

O resultado esperado contém `"package": "verified"` e `"boot": "unchanged"`.
O dry run verifica hashes, manifestos e arquiteturas; não testa áudio nem altera
boot. Não prossiga se houver erro. Não edite manifestos para contornar uma recusa.

Com a compatibilidade e a procedência confirmadas, na TV como root:

```sh
python3 global_install.py install-boot
```

Isso cria uma versão imutável em `/home/root/c5-dts-global/versions`, o link
`current` e o hook `/var/lib/webosbrew/init.d/zz-c5-dts-global`. Também prepara
o registro persistente do GStreamer com backup. A reprodução atual é preservada;
o comando não reinicia a TV. Repetir o mesmo pacote é permitido, mas uma versão
instalada diferente é recusada, sem sobrescrita automática.

Se a preparação do registro falhar depois de criar o hook, a instalação está
parcial: preserve a mensagem e resolva a causa antes do reboot. Para repetir
apenas essa etapa:

```sh
python3 /home/root/c5-dts-global/current/global_install.py prepare-cold-boot
```

Depois de instalação e preparação concluídas, faça um reboot completo da TV
pelo seu procedimento habitual e execute a [aceitação](Testing.md).
O campo `reboot_audio_verified: false` é conservador: o instalador não consegue
confirmar som no receiver. Não execute `boot` manualmente numa sessão em uso.

## Atualização ou migração do pacote nativo

O instalador global recusa o hook antigo `zz-c5-dts-passthrough`.
Se esse hook pertence à sua instalação nativa, desative-o com seu próprio
instalador, preservando o pacote para recuperação:

```sh
python3 /home/root/c5-dts-passthrough/current/installer.py disable-boot
```

Para substituir um pacote global, primeiro confira o dry run do novo pacote,
desative o boot do atual usando o comando abaixo e instale o novo. Os módulos
antigos permanecem residentes até reboot; não inicie controladores novos nessa
sessão. Se houver erro, não apague estado ou backups para forçar a instalação.

## Desativar e recuperar

```sh
python3 /home/root/c5-dts-global/current/global_install.py disable-boot
```

O comando confere a identidade do pacote/hook, restaura o registro persistente
somente se ele ainda corresponde ao registro preparado, e remove hook, link
`current` e estado de instalação. Preserva versões e a sessão atual; os módulos
continuam residentes até reboot. Se o registro ou hook mudou, a recusa preserva
os dados e exige inspeção. Nunca use `rmmod -f` ou reinicie serviços gerais de
áudio como substituto desse procedimento.

Para voltar a uma versão retida, use o instalador daquela pasta em `versions`
somente após desativar a instalação atual, conferir seu dry run e confirmar a
compatibilidade. Uma versão retida não é prova de compatibilidade com firmware novo.
