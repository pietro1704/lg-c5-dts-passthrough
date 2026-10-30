# Arquitetura e preparação de pacotes

## Componentes

| Componente | Responsabilidade |
| --- | --- |
| `src/gstdtspassthrough.c` | Parse DTS, empacotamento IEC61937 e preservação de bytes/PTS |
| `native/supervisor.py` | Autenticar worker, inode, PID, geração e época antes de confirmar rota |
| `native/module/` | Adaptador do kernel exato e controle das leases |
| `native/installer.py` | Pacote nativo, binds próprios e registro GStreamer |
| `native/hdmi/hdmi_supervisor.py` | Recuperação DTS core da entrada HDMI suportada |
| `native/hdmi/hdmi_hd_supervisor.py` | Supervisão DTS-HD com controlador de `experiments/hdmi-hd` |
| `native/hdmi/global_install.py` | Pacote único, preparação persistente e hook Homebrew |

MA 5.1 pode usar transporte de oito canais/192 kHz sem alterar os seis canais
codificados. O rótulo do receiver e o formato da portadora isoladamente não
comprovam identidade do bitstream nem todos os canais.

O plugin aguarda READY antes de liberar originais. A preparação do renderer é
limitada, restaura lipsync e não desloca PTS. Pausa, flush, troca de época ou
identidade invalidam provas anteriores. Endereços privados são específicos do
firmware; hashes e guardas não tornam outros aparelhos compatíveis.

## Preparação pelo mantenedor

Não existe build completo a partir apenas do download público. Obtenha
separadamente toolchain pré-compilada, headers/libs do alvo, headers FFmpeg 5 e
árvores de kernel C5 já preparadas. Não compile dependências implicitamente.
Antes de builds grandes, execute `python3 native/storage-preflight.py`:
o guard exige 5 GiB livres e remove somente `native/objects.retired` marcado
como saída reproduzível. Preserve modelos, fontes e evidência.

Os comandos de build do plugin e módulo estão no
[README](https://github.com/pietro1704/lg-c5-dts-passthrough/blob/main/README.md#build-and-test).
O plugin é ARM32; os módulos são ARM64. Um rebuild exige auditoria ABI/vermagic,
evidência de relógio vinculada aos hashes finais e aceitação no alvo.

`native/package-candidate.py` exige auditoria binária correspondente, supervisor
atual e artefatos próprios; sem `--clock-evidence` mantém bloqueios de ativação.
`--help` documenta os argumentos. O pacote nativo intermediário não é a release
global e não autoriza alegar aceitação de hardware.

O empacotador global recebe explicitamente os insumos:

```sh
python3 native/hdmi/build-global-package.py \
  --project /path/to/lg-c5-dts-passthrough \
  --native /path/to/verified-native-package \
  --output /path/to/new-global-package \
  --objcopy /path/to/llvm-objcopy \
  --hd-donor /path/to/verified-hd-donor \
  --hdmi-donor /path/to/verified-core-donor
```

Os caminhos são exemplos a substituir. Donors contêm `hdmi-manifest.json` e os
módulos; o donor HD precisa corresponder aos hashes aprovados no empacotador.
Sem `--hdmi-donor`, são usados os módulos locais de `native/hdmi/module`.
A saída deve ser nova. O empacotador verifica o pacote final e não instala nada.
A obtenção dos donors e a aceitação de um novo binário ainda são tarefas do
mantenedor; não há promessa de build reproduzível com toolchain arbitrária.

Hashes de manifestos conferem consistência, não autenticam o distribuidor.
Use artefatos de procedência conhecida e não modifique guardas para aprovar um
novo firmware. Nenhuma biblioteca de firmware ou mídia deve entrar no Git.
