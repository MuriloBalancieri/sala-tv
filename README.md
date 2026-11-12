# Sala TV

Biblioteca local de filmes e séries para enviar arquivos do computador ao reprodutor de uma TV Samsung pela rede. Interface em português, carrossel, temporadas e episódios, legendas SRT, capas do TMDB e retomada de reprodução.

**Validado em Windows 11 com Samsung UN55KU6000. Não é compatibilidade universal com Smart TVs.** O programa envia o vídeo por HTTP/DLNA; não transmite a tela. A imagem não é recodificada, mas o áudio fora do perfil compatível é convertido automaticamente para AAC LC estéreo a 48 kHz.

## Instalar e abrir

1. Instale **Python 3.12 para Windows**, incluindo o launcher `py`, pelo [site oficial](https://www.python.org/downloads/windows/).
2. Baixe o repositório como ZIP e extraia, ou clone com Git.
3. Execute `Instalar.cmd`. Ele cria uma instalação Python isolada na pasta `.venv`.
4. Execute `Abrir_Sala_TV.cmd`. A interface abre em `http://127.0.0.1:8768`.
5. Clique em **Configurar TV** e informe o IP da TV na sua rede local. O endereço inicial `192.168.1.100` é apenas um exemplo.
6. Adicione vídeos ou uma pasta e clique em **Assistir na TV**. Aceite a conexão na TV se ela solicitar.

O computador precisa permanecer ligado. Fechar a aba mantém o servidor ativo; **Encerrar** salva o progresso e fecha o programa. Se o firewall solicitar acesso, permita na sua rede privada. Não exponha a porta 8768 à internet.

## Recursos

- Filmes e episódios importados sem mover ou alterar os arquivos originais.
- Séries agrupadas em temporadas; nomes `S01E02` e `1x02` reconhecidos automaticamente.
- Controle remoto da TV para pausar e continuar, além dos controles da interface.
- Com um vídeo ativo na TV, use **Ir para o tempo** (`12:30` ou `01:12:30`) ou os botões de voltar/avançar 10 segundos. O programa confirma o tempo com a TV antes de salvar o novo ponto; o ajuste também funciona com o vídeo pausado, se o reprodutor aceitar DLNA Seek.
- Com FFmpeg instalado, o Sala TV verifica o áudio antes do envio. Se o áudio estiver fora do perfil AAC LC mono/estéreo a 44,1 ou 48 kHz, ele converte as faixas para AAC LC estéreo a 48 kHz e copia a imagem sem recompressão. A primeira reprodução pode demorar; as próximas reutilizam a cópia em `dados/audio-cache/`. Uma barra na interface mostra o progresso da conversão e o tempo decorrido. O original e as legendas são preservados. A adaptação não muda o codec do vídeo. Se o FFmpeg não for encontrado, o programa avisa e não envia o vídeo, em vez de arriscar um áudio incompatível.
- O envio à TV é confirmado: se o reprodutor não iniciar o vídeo, o programa tenta novamente e informa o erro em vez de dizer que enviou. Uma página aberta antes de reiniciar o programa recarrega sozinha quando detecta a nova sessão, evitando erros de autorização.
- Marcar um título como assistido apaga as cópias convertidas dele em `dados/audio-cache/`, liberando espaço. Se a cópia estiver tocando ou sendo preparada, a limpeza é adiada e repetida automaticamente quando a TV liberar o arquivo. Reassistir um título recria a cópia se o áudio ainda precisar de adaptação.
- Progresso consultado a cada 2 segundos; retomada cerca de 3 segundos antes do ponto salvo.
- Associação automática de SRT quando o nome corresponde ou há uma única legenda na pasta; seleção manual em **Detalhes**.
- Capas do TMDB com busca por título/ano e escolha manual para ambiguidades.
- Limpeza automática de cadastros e capas de vídeos apagados; discos indisponíveis preservam os títulos.

Pausa, avanço, codecs, áudio, legendas e retomada dependem do reprodutor da TV. Retomadas recusadas são informadas na interface e não apagam o ponto salvo. Renomear ou mover um vídeo é tratado como remoção do caminho antigo; adicione o novo caminho novamente.

## Configurar capas

Crie sua própria conta e obtenha uma chave ou token de leitura em [TMDB → Configurações → API](https://www.themoviedb.org/settings/api). Cole no campo **Capas e API**. A credencial é protegida pelo Windows para a conta atual e não é devolvida à interface.

Somente nomes e anos são enviados à API; vídeos e caminhos não são enviados. Imagens já baixadas ficam disponíveis localmente. O uso da API deve observar os [termos e a atribuição do TMDB](https://developer.themoviedb.org/docs/faq).

## Backup dos seus dados

O GitHub guarda **o código e as instruções**, não sua biblioteca pessoal. Para guardar títulos e progresso, faça um backup separado da pasta `dados/` com o programa encerrado. Ela contém o banco `biblioteca.sqlite`, capas e registros. Essa pasta está excluída do Git para proteger seus dados.

Ao recuperar em outra conta Windows ou outro computador, a biblioteca pode ser restaurada, mas será necessário configurar novamente a credencial TMDB, porque a proteção do Windows está vinculada à conta original. Atualize caminhos dos vídeos e o IP da TV quando necessário.

## Adaptar para outra TV

Veja [docs/ADAPTAR_OUTRA_TV.md](docs/ADAPTAR_OUTRA_TV.md), com descoberta do serviço, configuração do endereço de controle e roteiro de testes.

## Estrutura

- `app.py`: servidor HTTP, biblioteca SQLite, controle DLNA e acompanhamento de posição.
- `index.html`: interface local.
- `tmdb_covers.py`: API TMDB e proteção da credencial pelo Windows.
- `requirements.txt`: dependência usada para obter FFmpeg e gerar miniaturas.
- `assets/tmdb-logo.svg`: logo utilizado na documentação oficial do TMDB.
- `scripts/descobrir_tv.py`: consulta somente de leitura à descrição UPnP de uma TV.

Variáveis opcionais: `SALA_TV_DATA_DIR` para outra pasta de dados, `SALA_TV_TV_IP` para o IP inicial e `SALA_TV_FFMPEG` para um executável FFmpeg existente.

## Créditos

<img src="assets/tmdb-logo.svg" alt="TMDB" width="100">

This product uses the TMDB API but is not endorsed or certified by TMDB.

FFmpeg é distribuído por `imageio-ffmpeg`; seus componentes e licenças continuam pertencendo aos respectivos projetos. Python é necessário e não está incluído neste repositório. Não há vídeos, legendas, chaves de API ou capas da biblioteca publicados aqui.
