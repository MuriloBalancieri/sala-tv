# Adaptar o Sala TV para outro modelo

## 1. O que precisa existir na TV

A TV precisa funcionar como **UPnP/DLNA MediaRenderer**, aceitar arquivos por HTTP e oferecer o serviço `AVTransport`. Ter Wi-Fi ou ser uma Smart TV não garante isso.

O fluxo validado na Samsung UN55KU6000 usa:

```text
HTTP de controle: porta 9197
AVTransport: /upnp/control/AVTransport1
InstanceID: 0
Interface do computador: http://127.0.0.1:8768
Arquivo para a TV: http://IP_DO_COMPUTADOR:8768/media/ID/movie.mp4
```

Na outra TV, confirme esses dados na descrição UPnP; não copie o endereço Samsung sem verificar. A interface está restrita ao computador. O acesso aos vídeos é permitido para o IP configurado da TV e para o próprio computador.

## 2. Descobrir o serviço

Com a TV ligada e na mesma rede, execute:

```powershell
.venv\Scripts\python.exe scripts\descobrir_tv.py
```

O script envia uma consulta SSDP para renderizadores na rede e lê as descrições anunciadas. Não envia comandos de reprodução. Se multicast for bloqueado pelo roteador, obtenha o URL de descrição UPnP pela documentação ou por uma ferramenta de descoberta da sua rede e execute:

```powershell
.venv\Scripts\python.exe scripts\descobrir_tv.py --description-url "http://IP_DA_TV:PORTA/descricao.xml"
```

Anote fabricante, modelo, tipo/versão do serviço e `controlURL` completo. O exemplo acima deve ser substituído pelos dados reais; não é um caminho universal.

## 3. Configurar a conexão

Primeiro configure o IP pela interface e encerre o programa. A versão atual usa `AVTransport:1`. Para um endpoint HTTP no **mesmo IP da TV**, altere as configurações locais de porta e caminho:

```powershell
.venv\Scripts\python.exe scripts\configurar_controle.py --port 9197 --path /upnp/control/AVTransport1
```

Substitua pelos valores descobertos. Esses dados são gravados apenas no seu banco local. Se o serviço exigir HTTPS, autenticação, outro host, `InstanceID` diferente de 0 ou uma versão incompatível, adapte `soap()` em `app.py` antes de testar. Não desative validação TLS.

## 4. Roteiro de validação

Use um vídeo de teste e registre os resultados para aquele modelo:

1. **Entrega:** a TV lê `HEAD`, `GET` e `Range` no arquivo? O servidor responde `206` com `Content-Range` correto?
2. **Reprodução:** `SetAVTransportURI` aceita o DIDL-Lite? `Play` inicia imagem e áudio?
3. **Controle remoto:** pausar/continuar na TV muda `GetTransportInfo` e `GetPositionInfo`?
4. **Persistência:** pause após pelo menos 30 segundos, encerre e abra o programa; o ponto continua salvo?
5. **Retomada:** `Seek` com `Unit=REL_TIME` aceita o tempo salvo? O programa confirma o tempo retornado em vez de assumir sucesso.
6. **Legendas:** a TV aceita SRT externo? Pode ser preciso habilitar a legenda no menu da TV.
7. **Interrupções:** desligue a TV ou interrompa a rede; o último ponto válido deve permanecer salvo. Um retorno de posição zero após `Stop` não deve apagar o histórico.
8. **Limpeza:** apagar um vídeo de teste deve remover somente seu cadastro e capas; desconectar o disco deve preservar os títulos.

## 5. Particularidades do código atual

As legendas externas usam `sec:CaptionInfoEx` no DIDL-Lite e `CaptionInfo.sec` no HTTP. Esses mecanismos foram preparados para Samsung e podem ser ignorados por outras marcas. Ajuste `play()` e `Handler.deliver_media()` conforme o reprodutor de destino. A associação de SRT no banco não confirma que toda TV consiga exibi-lo.

O programa envia o arquivo original: se a TV não decodificar HEVC/x265, áudio multicanal, MKV ou outro formato, use um arquivo compatível ou implemente transcodificação separadamente. O comportamento de `Range`, duração e `Seek` precisa ser revisto se for adicionada transcodificação ao vivo.

O retorno HTTP 500 após `Play` é tolerado porque a Samsung validada pode iniciar automaticamente ao receber a URI. Isso não significa que um erro 500 seja sucesso em outro modelo. Confira estado, posição e reprodução visível.

Aplique mudanças primeiro em uma cópia do projeto e mantenha backup de `dados/` com o programa encerrado. Registre fabricante, modelo, firmware e resultados no README quando validar outra TV. Não anuncie compatibilidade sem um teste real.
