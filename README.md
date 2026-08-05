# NFCe Trigger

Sincroniza arquivos XML de NFC-e modificados no mês atual entre pastas de origem, destino e uma pasta intermediária (por exemplo, Google Drive). O Ansible instala e executa o script diretamente no Windows gerenciado.

## Implantação com Ansible

O controlador Ansible deve ser Linux, WSL ou um contêiner; o Windows gerenciado precisa de WinRM e Python instalado.

1. Instale as coleções:

   ```bash
   ansible-galaxy collection install -r ansible/requirements.yml
   ```

2. No Semaphore, cadastre o inventário com os grupos `nfce_windows` e, somente em homologação, `pdv_windows`. Configure nele as variáveis `nfce_*` exigidas pelo playbook.

3. Armazene a credencial WinRM no Key Store do Semaphore e vincule-a ao inventário. Nunca versione credenciais.

4. Valide a conexão e aplique:

   ```bash
   ansible nfce_windows -i ansible/inventory.yml -m ansible.windows.win_ping
   ansible-playbook -i ansible/inventory.yml ansible/playbook.yml --check
   ansible-playbook -i ansible/inventory.yml ansible/playbook.yml
   ```

Cada execução do playbook garante a instalação do Python, roda o sincronizador pelo WinRM e exibe o resultado no terminal do controlador. O instalador de 64 bits é obtido do site oficial do Python e validado por SHA-256. O playbook também remove a antiga tarefa `NFCeTrigger` do Agendador do Windows, caso exista.

A versão do Python e seu SHA-256 são mantidos juntos em `nfce_python_installers`, no playbook. Uma versão não cadastrada é rejeitada antes do download, evitando validar um instalador com o checksum de outra versão.

A execução do sincronizador usa `runas` com a mesma credencial do WinRM. Isso cria um logon apto a acessar compartilhamentos de rede a partir do Windows central e evita a limitação de salto duplo do NTLM.

Como a verificação por SHA-256 e a espera pelo Google Drive podem ultrapassar o limite padrão do WinRM, o playbook usa 600 segundos para a conexão. Esse valor pode ser alterado no Semaphore pela variável `nfce_winrm_connection_timeout`.

Cada execução aparece no histórico do Semaphore e também é gravada no Windows central em `C:\NFCe\trigger\log\nfce_trigger.log`. O arquivo registra início, cópias, avisos, resumo, duração e erros. Ao atingir 5 MB, ele é rotacionado automaticamente, mantendo até 10 arquivos anteriores. A execução força UTF-8 para preservar os acentos na saída do Semaphore.

Os hosts incluídos em `pdv_windows` são considerados parte do laboratório: o playbook prepara a pasta, o compartilhamento e o XML fictício diretamente nesses PDVs. Em produção, não inclua esse grupo no inventário. Defina também `nfce_prepare_fictitious_data` obrigatoriamente no Semaphore; `true` cria dados adicionais nas origens configuradas no servidor central e `false` desabilita somente essa preparação adicional.

O `ansible/playbook.yml` primeiro prepara a pasta, o XML e o compartilhamento dos hosts presentes no grupo `pdv_windows`; depois instala e executa a sincronização nos hosts do grupo `nfce_windows`. Em produção, omita o grupo `pdv_windows` do inventário para que o Ansible não altere as origens reais.

Para recorrência, programe a chamada de `ansible-playbook` no controlador WSL ou utilize uma plataforma como AWX. O Windows não agenda nem inicia o sincronizador por conta própria.

## Integridade da sincronização

Os arquivos são comparados por tamanho e SHA-256. Uma cópia é gravada primeiro em um arquivo temporário no mesmo diretório, validada e publicada atomicamente. Arquivos existentes com conteúdo divergente são corrigidos. Se duas origens apresentarem o mesmo nome de XML, a execução é interrompida e informa todas as origens envolvidas.

Quando a pasta temporária não está disponível, o sincronizador tenta criá-la. No Windows, se a unidade continuar ausente, inicia o Google Drive e aguarda até 60 segundos. Qualquer falha da configuração, leitura, validação, cópia, heartbeat ou inicialização gera uma tentativa de alerta por e-mail e encerra a execução com código diferente de zero.

### Semaphore

O arquivo `ansible/inventory.yml` é local e não é enviado ao Git. Cadastre as variáveis `nfce_*` no inventário estático ou no ambiente associado ao template.

## Alertas por e-mail

Credenciais não ficam no código. Configure no ambiente da conta que executa o processo:

- `NFCE_SMTP_USER`
- `NFCE_SMTP_PASSWORD`
- `NFCE_ALERT_RECIPIENT`
- `NFCE_SMTP_HOST` e `NFCE_SMTP_PORT` (opcionais)
