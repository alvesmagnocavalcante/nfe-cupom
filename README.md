# NFCe Trigger

Sincroniza arquivos XML de NFC-e modificados no mês atual entre pastas de origem, destino e uma pasta intermediária (por exemplo, Google Drive). O Ansible instala e executa o script diretamente no Windows gerenciado.

## Implantação com Ansible

O controlador Ansible deve ser Linux, WSL ou um contêiner; o Windows gerenciado precisa de WinRM e Python instalado.

1. Instale as coleções:

   ```bash
   ansible-galaxy collection install -r ansible/requirements.yml
   ```

2. Crie `ansible/inventory.yml` e `ansible/group_vars/nfce_windows.yml` a partir dos respectivos arquivos `.example.yml`, mantendo as cópias fora do Git.

3. Proteja a senha do inventário com Ansible Vault ou informe-a em tempo de execução. Nunca versione credenciais.

4. Valide a conexão e aplique:

   ```bash
   ansible nfce_windows -i ansible/inventory.yml -m ansible.windows.win_ping
   ansible-playbook -i ansible/inventory.yml ansible/playbook.yml --check
   ansible-playbook -i ansible/inventory.yml ansible/playbook.yml
   ```

Cada execução do playbook garante a instalação do Python, roda o sincronizador pelo WinRM e exibe o resultado no terminal do controlador. O instalador de 64 bits é obtido do site oficial do Python e validado por SHA-256. O playbook também remove a antiga tarefa `NFCeTrigger` do Agendador do Windows, caso exista.

A execução do sincronizador usa `runas` com a mesma credencial do WinRM. Isso cria um logon apto a acessar compartilhamentos de rede a partir do Windows central e evita a limitação de salto duplo do NTLM.

Para um ambiente de homologação, defina `nfce_prepare_fictitious_data: true` nas variáveis do grupo. O Ansible criará as pastas configuradas e um XML fictício em cada origem antes da sincronização. Mantenha essa opção ausente ou como `false` em produção.

No laboratório com um Windows PDV separado, execute `ansible/laboratorio.yml`. Ele recria a pasta, o XML e o compartilhamento do PDV por meio de `criar_pastas.yml` e, em seguida, executa `ansible/playbook.yml` no Windows central. Em produção, continue usando somente `ansible/playbook.yml`.

Para recorrência, programe a chamada de `ansible-playbook` no controlador WSL ou utilize uma plataforma como AWX. O Windows não agenda nem inicia o sincronizador por conta própria.

### Semaphore

Os arquivos `ansible/inventory.yml` e `ansible/group_vars/nfce_windows.yml` são locais e não são enviados ao Git. Ao usar Semaphore, cadastre as variáveis `nfce_*` no inventário estático ou no ambiente associado ao template. Arquivos terminados em `.example.yml` servem apenas como modelo e não são carregados automaticamente.

## Alertas por e-mail

Credenciais não ficam no código. Configure no ambiente da conta que executa o processo:

- `NFCE_SMTP_USER`
- `NFCE_SMTP_PASSWORD`
- `NFCE_ALERT_RECIPIENT`
- `NFCE_SMTP_HOST` e `NFCE_SMTP_PORT` (opcionais)
