"""Carga los datos de ejemplo de seed/*.json en las tablas de DynamoDB.

Uso (con las credenciales del Learner Lab en el perfil upc-moviles):
    python3 scripts/generar_datos.py                                    # agenda alrededor de hoy
    AWS_PROFILE=upc-moviles python3 scripts/cargar_datos.py --limpiar

--limpiar borra antes todos los items de las tablas (deja solo los datos de ejemplo).
Sin --limpiar, los items con la misma clave se sobrescriben y el resto se conserva.
"""
import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

import boto3

REGION = os.environ.get('AWS_REGION', 'us-east-1')
# archivo de seed -> (tabla destino, clave)
TABLAS = {'medicos': 'cmp', 'pacientes': 'id', 'citas': 'id', 'resultados': 'id'}


def limpiar(tabla, clave):
    borrados = 0
    kwargs = {'ProjectionExpression': '#k', 'ExpressionAttributeNames': {'#k': clave}}
    with tabla.batch_writer() as batch:
        while True:
            response = tabla.scan(**kwargs)
            for item in response.get('Items', []):
                batch.delete_item(Key={clave: item[clave]})
                borrados += 1
            if 'LastEvaluatedKey' not in response:
                return borrados
            kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--limpiar', action='store_true', help='borra los items existentes antes de cargar')
    parser.add_argument('--dir', default=str(Path(__file__).resolve().parent.parent / 'seed'), help='carpeta con los JSON')
    args = parser.parse_args()

    dynamodb = boto3.resource('dynamodb', region_name=REGION)
    for nombre, clave in TABLAS.items():
        tabla = dynamodb.Table(nombre)
        if args.limpiar:
            print(f'{nombre}: {limpiar(tabla, clave)} items borrados')
        # DynamoDB no acepta float: los decimales se leen como Decimal
        items = json.loads((Path(args.dir) / f'{nombre}.json').read_text(encoding='utf-8'), parse_float=Decimal)
        with tabla.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        print(f'{nombre}: {len(items)} items cargados')


if __name__ == '__main__':
    main()
