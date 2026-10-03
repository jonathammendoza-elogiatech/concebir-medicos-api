"""Carga los datos de ejemplo de seed/*.json en las tablas de DynamoDB.

Uso (con las credenciales del Learner Lab en el perfil upc-moviles):
    AWS_PROFILE=upc-moviles python3 scripts/cargar_datos.py

Si un item ya existe con la misma clave, se sobrescribe.
"""
import json
import os
from decimal import Decimal
from pathlib import Path

import boto3

REGION = os.environ.get('AWS_REGION', 'us-east-1')
SEED = Path(__file__).resolve().parent.parent / 'seed'
# archivo de seed -> tabla destino (mismos nombres por defecto)
TABLAS = ['medicos', 'pacientes', 'citas', 'resultados']


def main():
    dynamodb = boto3.resource('dynamodb', region_name=REGION)
    for nombre in TABLAS:
        # DynamoDB no acepta float: los decimales se leen como Decimal
        items = json.loads((SEED / f'{nombre}.json').read_text(encoding='utf-8'), parse_float=Decimal)
        with dynamodb.Table(nombre).batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        print(f'{nombre}: {len(items)} items cargados')


if __name__ == '__main__':
    main()
