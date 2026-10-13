"""Configura porta e caminho AVTransport no banco local com o programa fechado."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--path',required=True)
    args=parser.parse_args()
    if not 1<=args.port<=65535 or not args.path.startswith('/') or args.path.startswith('//') or any(x in args.path for x in ('\r','\n','#')):
        parser.error('Informe uma porta valida e um caminho HTTP iniciado por /.')
    app.initialize()
    with app.connect() as db:
        db.execute("UPDATE settings SET value=? WHERE key='control_port'",(str(args.port),))
        db.execute("UPDATE settings SET value=? WHERE key='control_path'",(args.path,))
    print('Endereco de controle atualizado. Abra o Sala TV para testar.')

if __name__=='__main__': main()
