# Diagnóstico e recuperação

| Sintoma | Conferir / ação |
| --- | --- |
| Falta `global-manifest.json` | Provavelmente está usando o arquivo de fontes; é necessário um pacote global construído |
| `wrong target` / arquitetura / hash | Pare; confira kernel, firmware e integridade. Não edite hashes para passar |
| `older native boot hook still active` | Desative a instalação nativa pelo instalador dela; veja Instalação |
| `different existing installation; no overwrite` | O upgrade não é in-place; desative o pacote atual antes de instalar outro |
| Falha em `prepare-cold-boot` | Preserve backup/estado e resolva a causa; repita a preparação antes do reboot |
| Nuvio omite DTS ao reabrir | Confira preparação do registro e reboot completo; não reinicie serviços gerais de áudio |
| Blu-ray fica em PCM | Confira Bitstream no player de Blu-ray, faixa, eARC e capacidade MA do receiver |
| Jogo não sai em DTS | Confira prioridade de áudio da PS5 e HDMI3; configuração do Blu-ray é separada |
| Menu da TV suspende recuperação | O controlador espera a fonte selecionada voltar antes de agir |
| Endpoint existente / controlador incerto | Não empilhe controladores; preserve logs e investigue antes de reboot planejado |

No alvo, `/run/c5-dts-global/started.json` registra resultado do boot;
`/run/c5-dts-global/hdmi/controller.log` e
`/run/c5-dts-global/hdmi-hd/controller.log` registram os controladores HDMI.
Ausência de `started.json` não prova ausência de processos: uma ativação parcial
pode ter ocorrido. Um `hdmi_hd_error` não deve ser interpretado como sucesso HD.

O instalador não desliga/reinicia a TV. Para desativação persistente use o
[procedimento documentado](Installation.md); módulos que já atenderam áudio
ficam residentes até reboot. Preserve versões e backups. Não force unload,
não reutilize PIDs de sessões antigas e não altere rotas manualmente durante
reprodução nativa/HDMI.

Em relatos públicos informe versão, modelo, firmware, kernel, receiver,
formato/fonte e passos de reprodução. Remova credenciais, IPs, caminhos pessoais,
identificadores de mídia e dumps proprietários. Não publique logs completos sem revisão.
