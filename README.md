# NFCe Trigger

Sincroniza arquivos XML de NFC-e modificados no mês atual entre pastas de origem, destino e uma pasta intermediária (por exemplo, Google Drive). O Ansible instala e executa o script diretamente no Windows gerenciado.

## Implantação com Ansible

O controlador Ansible deve ser Linux, WSL ou um contêiner; o Windows gerenciado precisa de WinRM e Python instalado.

1. Instale as coleções:

   ```bash
   ansible-galaxy collection install -r ansible/requirements.yml
   ```

2. No Semaphore, cadastre o inventário com o grupo `nfce_windows`. Configure nele as variáveis `nfce_*` exigidas pelo playbook, usando `ansible/variables.example.yml` como modelo. Cada hotel deve possuir um ambiente/template próprio e um `nfce_instance` único.

3. Armazene a credencial WinRM no Key Store do Semaphore e vincule-a ao inventário. Nunca versione credenciais.

4. Valide a conexão e aplique:

   ```bash
   ansible nfce_windows -i ansible/inventory.yml -m ansible.windows.win_ping
   ansible-playbook -i ansible/inventory.yml ansible/playbook.yml --check
   ansible-playbook -i ansible/inventory.yml ansible/playbook.yml
   ```

Cada execução do playbook garante a instalação do Python, roda o sincronizador pelo WinRM e exibe o resultado no terminal do controlador. O instalador de 64 bits é obtido do site oficial do Python e validado por SHA-256. O playbook também remove a antiga tarefa `NFCeTrigger` do Agendador do Windows, caso exista.

No modo `--check`, o playbook valida as variáveis e informa as alterações de infraestrutura previstas, mas não confirma a versão final do Python nem executa o sincronizador.

A versão do Python e seu SHA-256 são mantidos juntos em `nfce_python_installers`, no playbook. Uma versão não cadastrada é rejeitada antes do download, evitando validar um instalador com o checksum de outra versão.

A execução do sincronizador usa `runas` com a mesma credencial do WinRM. Isso cria um logon apto a acessar compartilhamentos de rede a partir do Windows central e evita a limitação de salto duplo do NTLM.

Como a verificação por SHA-256 e a espera pelo Google Drive podem ultrapassar o limite padrão do WinRM, o playbook usa 600 segundos para a conexão. Esse valor pode ser alterado no Semaphore pela variável `nfce_winrm_connection_timeout`.

Cada execução aparece no histórico do Semaphore e também é gravada no Windows central em `C:\NFCe\trigger\<nfce_instance>\log\nfce_trigger.log`, salvo quando `nfce_install_dir` é definido explicitamente. O arquivo registra início, cópias, avisos, resumo, duração e erros. Ao atingir 5 MB, ele é rotacionado automaticamente, mantendo até 10 arquivos anteriores. A execução força UTF-8 para preservar os acentos na saída do Semaphore.

### Vários hotéis no mesmo Windows

O `nfce_instance` identifica tecnicamente cada hotel e aceita apenas letras,
números, hífen e sublinhado. Quando `nfce_install_dir` não é informado, ele
também separa automaticamente as instalações:

```text
C:\NFCe\trigger\charme
C:\NFCe\trigger\magna
```

Configuração, log e arquivo de monitoramento ficam dentro da instalação de cada
hotel. O heartbeat recebe o identificador no nome, por exemplo
`ultima_execucao_charme.txt`, evitando colisão mesmo quando dois hotéis usam a
mesma `nfce_heartbeat_destination`. Os diretórios de destino e temporário devem
ser definidos separadamente para cada hotel no Semaphore.

As pastas de origem e seus XMLs devem existir antes da execução. O playbook não cria, altera nem compartilha as origens; ele apenas instala o sincronizador no host central, garante os diretórios de destino, heartbeat e logs, e então executa a cópia.

Para recorrência, programe a chamada de `ansible-playbook` no controlador WSL ou utilize uma plataforma como AWX. O Windows não agenda nem inicia o sincronizador por conta própria.

## Integridade da sincronização

Os arquivos são comparados por tamanho e SHA-256. Uma cópia é gravada primeiro em um arquivo temporário no mesmo diretório, validada e publicada atomicamente. Arquivos existentes com conteúdo divergente são corrigidos. Se duas origens apresentarem o mesmo nome de XML, a execução é interrompida e informa todas as origens envolvidas.

Quando a pasta temporária não está disponível, o sincronizador tenta criá-la. No Windows, se a unidade continuar ausente, inicia o Google Drive e aguarda até 60 segundos. Qualquer falha da configuração, leitura, validação, cópia, heartbeat ou inicialização gera uma tentativa de alerta por e-mail e encerra a execução com código diferente de zero.

### Semaphore

O arquivo `ansible/inventory.yml` é local e não é enviado ao Git. Cadastre as variáveis `nfce_*` no inventário estático ou no ambiente associado ao template. Para vários hotéis, crie um ambiente/template por hotel, reutilizando o mesmo playbook e atribuindo um `nfce_instance` diferente a cada um.

Os arquivos `ansible/variables.charme.example.yml` e
`ansible/variables.magna.example.yml` mostram os dois ambientes separados. Não
combine os dois conjuntos no mesmo ambiente do Semaphore, pois as variáveis de
um hotel substituiriam as do outro.

## Alertas por e-mail

Credenciais não ficam no código. Configure no ambiente da conta que executa o processo:

- `NFCE_SMTP_USER`
- `NFCE_SMTP_PASSWORD`
- `NFCE_ALERT_RECIPIENT`
- `NFCE_SMTP_HOST` e `NFCE_SMTP_PORT` (opcionais)
