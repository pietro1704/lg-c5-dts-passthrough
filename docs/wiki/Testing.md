# Testes e aceitação

## Verificação automática

Execute as verificações do workflow `Public source checks`:

```sh
python3 scripts/check-publication.py
bash -n build-plugin.sh
node --check verify-iec.mjs
node --test tests/verify-iec.test.mjs
bash -n native/build-module.sh
bash -n native/build-clock-isolation.sh
python3 native/checks.py
sh -n install.sh remove.sh
python3 tests/test-tv-package.py
```

O check de publicação examina os bytes no **index do Git**; adicione somente
arquivos revisados antes de executá-lo localmente. A CI roda em Linux.
Os testes de host usam Python 3, Node.js e Clang com ASan/UBSan; não acessam a TV
nem carregam módulos. Não é necessário compilar dependências para executá-los.

| Área | Evidência automatizada | Limite |
| --- | --- | --- |
| READY e preparação | Código C extraído da implementação; sucesso, timeout, pausa, flush, épocas, identidade, falhas e cancelamento | Chamadas de vendor são modeladas |
| Leases e políticas | C com sanitizers; geração, replay, exaustão e portas | Não executa todo o módulo no kernel |
| Supervisor nativo | Worker/inode/PID, grant, rota, READY e recusa de identidades erradas | Não substitui firmware real |
| Instalação e pacotes | Hashes, symlinks, rollback, estado próprio, registro e falhas | Nem toda combinação de interrupção de boot está coberta |
| HDMI | Estados core/HD, troca de fonte, cancelamento, receiver, HPD e controles ALSA | Transporte e áudio físico precisam de teste no alvo |
| IEC61937 | Verificador de bytes do transporte | Não executa todo o parser GStreamer em CI |

Os ensaios `native/test-clock-target.py` e `native/test-cold-registry-target.py`
são específicos do alvo. O primeiro dispõe de modo isolado com memory sinks e
adaptador de teste; veja o README e `--help`. Não os confunda com CI de host.
Nunca carregue o adaptador de teste em um player real.

A suíte `tests/test-tv-package.py` executa os scripts de shell extraídos do arquivo
de release e exercita instalação, repetição, remoção e restauração com arquivos
temporários. Usa as funções reais de backup/journal, substituindo somente acesso
a hardware e inspeção GStreamer. Cobre falha/cancelamento, kernel errado,
identidades alteradas, locks, instalação existente, arquivo parcial e publicação
sem logs privados. Os novos scripts não foram executados na TV real.

## Aceitação de cada novo pacote

Registre hashes do plugin, módulos e manifestos, modelo/firmware, receiver,
fonte, configuração e resultado. Guarde logs e mídia privadamente.

1. Após reboot completo, abrir o Nuvio original e reproduzir DTS e DTS-HD MA.
2. Trocar DD → MA e MA → DD; verificar som, avanço de vídeo e lipsync.
3. Pausar/retomar repetidamente, buscar e fechar/reabrir com novo worker.
4. Confirmar DTS de jogos na PS5 e Blu-ray com Bitstream e faixa correta.
5. Verificar PCM/Dolby, troca de fonte, menu, standby e reconexão do receiver.
6. Confirmar canais com mídia apropriada e comparar payload/PTS nos ensaios
   isolados. Uma indicação MA no receiver sozinha não é aprovação.
7. Repetir desativação/recuperação em uma janela de manutenção autorizada.

Em 30/10/2026 houve aceitação no alvo de reboot, Nuvio 1.2.9, DD → MA,
três pausas/retomadas, reabertura, jogos DTS e Blu-ray DD → MA. O ensaio de
relógio preservou 1.867.776 bytes originais e PTS, com diferença do relógio de
referência dentro de 0,05 s. Isso não foi repetido pela criação desta wiki.

**Não está tudo coberto.** Faltam matriz completa de fontes/receivers e Dolby,
estabilidade prolongada, medição física de A/V sync e tempo de início dos jogos.
Não há percentual global de cobertura medido. Testes aprovados reduzem o risco;
não garantem ausência de regressões no firmware e no equipamento.
