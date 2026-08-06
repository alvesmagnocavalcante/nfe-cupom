# NFCe Trigger

Aplicação para coletar XMLs de NFC-e dos PDVs de um hotel e copiá-los para dois
locais:

1. um diretório de destino no servidor Windows que executa a aplicação;
2. um diretório temporário, que pode ser uma unidade do Google Drive.

O Semaphore executa o playbook Ansible, escolhe o servidor pelo inventário,
envia as variáveis do hotel e acompanha o resultado. O nome informado em
`nfce_hotel` identifica o hotel nos logs e alertas, mas não escolhe o servidor.

## Fluxo

```text
Template do hotel no Semaphore
        |
        +-- Inventário --------> servidor Windows via WinRM
        +-- Ambiente JSON -----> variáveis nfce_*
        +-- Playbook ----------> ansible/playbook.yml
                                  |
PDVs / compartilhamentos --------+--> destino no servidor
                                  +--> diretório temporário
                                  +--> heartbeat
```

O sentido da cópia é sempre:

```text
nfce_sources -> nfce_destination_directory
             -> nfce_temporary_directory
```

Se o destino começar com `C:\`, ele pertence ao servidor selecionado pelo
inventário. Para gravar em outro computador, informe um caminho UNC e garanta as
permissões necessárias.

## Organização do projeto

```text
.
|-- main.py                         # ponto de entrada
|-- nfce_trigger/
|   |-- application.py              # argumentos, logging e execução
|   |-- config.py                   # leitura do config.ini
|   |-- files.py                    # SHA-256 e cópia atômica
|   |-- google_drive.py             # disponibilidade do Google Drive
|   |-- sync.py                     # seleção e sincronização dos XMLs
|   `-- alerts.py                   # alertas SMTP
|-- ansible/
|   |-- playbook.yml
|   |-- requirements.yml
|   |-- templates/config.ini.j2
|   `-- variables.*.example.yml
`-- tests/test_main.py
```

A aplicação utiliza apenas a biblioteca padrão do Python. As dependências em
`ansible/requirements.yml` são usadas pelo controlador Ansible, não pelo
servidor Windows.

## Requisitos

### Controlador

- Semaphore, Linux, WSL ou outro ambiente capaz de executar Ansible;
- acesso WinRM ao servidor Windows;
- coleções de `ansible/requirements.yml` instaladas.

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

### Servidor Windows

- WinRM configurado;
- acesso de leitura aos compartilhamentos dos PDVs;
- acesso de escrita aos destinos configurados;
- Google Drive instalado e autenticado somente quando a pasta temporária usa
  uma unidade do Google Drive.

O Python não precisa estar previamente instalado. O playbook instala e valida o
Python 3.13.14 em `C:\Python313` por padrão. O download usa o site oficial e o
SHA-256 cadastrado no próprio playbook.

## Configuração no Semaphore

Use um conjunto independente para cada hotel:

```text
Hotel Charme
|-- Inventário: servidor do Charme
|-- Ambiente: variáveis JSON do Charme
`-- Template: inventário + ambiente + ansible/playbook.yml

Hotel Magna
|-- Inventário: servidor do Magna
|-- Ambiente: variáveis JSON do Magna
`-- Template: inventário + ambiente + ansible/playbook.yml
```

Os hotéis podem usar servidores diferentes ou compartilhar um servidor. Em
ambos os casos, cada hotel deve possuir um `nfce_instance` exclusivo.

### Inventário

O playbook executa no grupo `nfce_windows`. Um inventário dedicado ao Charme,
por exemplo, pode conter:

```ini
[nfce_windows]
servidor_charme ansible_host=10.197.0.50
```

O inventário é o responsável por selecionar o servidor. Não coloque servidores
de hotéis diferentes no mesmo grupo para um template que contém variáveis de
apenas um hotel.

Cadastre a conta e a senha WinRM no Key Store do Semaphore e vincule a
credencial ao inventário/template. Não coloque `ansible_password` no repositório
nem no JSON do ambiente.

### Ambiente JSON

Exemplo para o Charme:

```json
{
  "nfce_instance": "charme",
  "nfce_hotel": "Charme",
  "nfce_sources": [
    {
      "name": "caps",
      "path": "\\\\10.197.1.10\\charme.servidor-caps.nfce"
    },
    {
      "name": "charme-11",
      "path": "\\\\10.197.0.11\\charme.charme-11.nfce"
    }
  ],
  "nfce_destination_directory": "C:\\NFCe\\dados\\charme\\destino",
  "nfce_temporary_directory": "H:\\Meu Drive\\Robo_importsat_cmflex\\Charme",
  "nfce_heartbeat_destination": "H:\\Meu Drive\\Robo_importsat_cmflex\\Monitoramento",
  "nfce_python_dir": "C:\\Python313",
  "nfce_python_version": "3.13.14",
  "nfce_winrm_connection_timeout": 600
}
```

No JSON, caminhos Windows precisam escapar a barra invertida:

```text
C:\NFCe\dados       -> "C:\\NFCe\\dados"
\\servidor\NFCe     -> "\\\\servidor\\NFCe"
```

Exemplos adicionais:

- `ansible/variables.example.yml`: modelo genérico;
- `ansible/variables.charme.example.yml`: exemplo do Charme;
- `ansible/variables.magna.example.yml`: exemplo do Magna.

Esses arquivos usam YAML como referência. No campo de variáveis do ambiente do
Semaphore, use o JSON equivalente.

### Variáveis obrigatórias

| Variável | Função |
|---|---|
| `nfce_instance` | Identificador técnico único; aceita letras, números, `_` e `-` |
| `nfce_hotel` | Nome apresentado nos logs e alertas |
| `nfce_sources` | Lista de origens com `name` e `path` |
| `nfce_destination_directory` | Primeiro destino dos XMLs |
| `nfce_temporary_directory` | Segundo destino dos XMLs |
| `nfce_heartbeat_destination` | Destino do arquivo de última execução |

### Variáveis opcionais

| Variável | Padrão | Observação |
|---|---|---|
| `nfce_install_dir` | `C:\NFCe\trigger\<nfce_instance>` | Sobrescreve o diretório da instalação |
| `nfce_python_dir` | `C:\Python313` | Diretório onde o Python é validado/instalado |
| `nfce_python_version` | `3.13.14` | Somente versões cadastradas no playbook são aceitas |
| `nfce_winrm_connection_timeout` | `600` | Timeout da conexão WinRM em segundos |

As variáveis `nfce_python` e `nfce_prepare_fictitious_data` não são utilizadas
pelo código ou pelo playbook atual.

## Template de execução

Cada template do Semaphore deve associar:

- o repositório deste projeto;
- o playbook `ansible/playbook.yml`;
- o inventário que contém o servidor correto;
- o ambiente JSON do mesmo hotel;
- a credencial WinRM correspondente.

Ao executar o template, o playbook:

1. valida as variáveis;
2. verifica ou instala a versão suportada do Python;
3. cria os diretórios de instalação, destino, heartbeat e log;
4. instala `main.py` e o pacote `nfce_trigger`;
5. remove o antigo `main_trigger.py`, se existir;
6. gera `config/config.ini` a partir das variáveis;
7. executa a sincronização com `runas` usando a conta WinRM;
8. mostra a saída no histórico do Semaphore.

No modo `--check`, o playbook valida as entradas e simula as alterações, mas não
executa a sincronização.

## Estrutura instalada

Sem `nfce_install_dir`, uma instância chamada `charme` será instalada em:

```text
C:\NFCe\trigger\charme\
|-- main.py
|-- nfce_trigger\
|-- config\config.ini
`-- log\
    |-- nfce_trigger.log
    `-- ultima_execucao_charme.txt
```

Outra instância, como `magna`, usa `C:\NFCe\trigger\magna`. Isso impede que
configurações e logs sejam sobrescritos quando dois hotéis compartilham o mesmo
Windows.

O heartbeat é copiado para `nfce_heartbeat_destination` mantendo o nome
`ultima_execucao_<nfce_instance>.txt`.

## Regras da sincronização

- somente arquivos `.xml` diretamente dentro de cada origem são considerados;
- subdiretórios não são percorridos;
- somente XMLs cuja data de modificação pertence ao mês atual são selecionados;
- o mesmo conjunto é copiado para o destino e para o diretório temporário;
- arquivos iguais são ignorados após comparação de tamanho e SHA-256;
- arquivos ausentes ou divergentes são copiados;
- a cópia é feita em arquivo temporário, validada e publicada atomicamente;
- XMLs existentes no destino não são apagados;
- se duas origens tiverem XMLs com o mesmo nome, a execução falha antes da
  cópia;
- o heartbeat só é atualizado depois que as duas cópias terminam sem erro.

Quando a pasta temporária não existe, a aplicação tenta criá-la. Se estiver no
Windows e a unidade continuar indisponível, tenta iniciar o Google Drive e
aguarda até 60 segundos.

### Limitação atual das origens

Origens indisponíveis geram um aviso no log. A execução continua quando pelo
menos uma origem está disponível e só falha quando nenhuma origem pode ser
acessada. Portanto, o operador deve conferir os avisos no Semaphore e no log;
uma execução concluída pode não ter consultado todos os PDVs configurados.

## Logs e alertas

O log padrão de cada instância fica em:

```text
C:\NFCe\trigger\<nfce_instance>\log\nfce_trigger.log
```

O arquivo é rotacionado ao atingir 5 MB e mantém até 10 versões anteriores.

Em caso de erro, a aplicação tenta enviar um alerta SMTP. As variáveis abaixo
devem existir no ambiente da conta Windows que executa o processo:

- `NFCE_SMTP_USER`;
- `NFCE_SMTP_PASSWORD`;
- `NFCE_ALERT_RECIPIENT`;
- `NFCE_SMTP_HOST` (opcional, padrão `smtp.gmail.com`);
- `NFCE_SMTP_PORT` (opcional, padrão `587`).

Essas variáveis são variáveis de ambiente do Windows e não variáveis `nfce_*`
do ambiente JSON do Semaphore. Se não estiverem configuradas, a falha continua
registrada no log, mas o e-mail não é enviado.

## Execução e testes locais

Execução manual:

```bash
python main.py \
  --config config/config.ini \
  --log-file log/nfce_trigger.log
```

Simulação sem copiar ou criar arquivos:

```bash
python main.py \
  --config config/config.ini \
  --log-file log/nfce_trigger.log \
  --dry-run
```

Testes automatizados:

```bash
python -m unittest discover -s tests -v
```

Os arquivos dentro de `config/` e o inventário local
`ansible/inventory.yml` são ignorados pelo Git. Não versione credenciais ou
configurações locais de produção.
