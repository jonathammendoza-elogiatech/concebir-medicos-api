import base64
import json
import logging
import operator
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from functools import reduce

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Inicialización de DynamoDB
dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME', 'resultados')
table = dynamodb.Table(TABLE_NAME)

KEY = 'id'
TIPOS = {'LABORATORIO', 'GENETICA'}
ESTADOS = {'EN_RANGO', 'FUERA_DE_RANGO', 'PENDIENTE'}
OBLIGATORIOS = ['pacienteId', 'tipo', 'nombre', 'estado']
CAMPOS = ['pacienteId', 'tipo', 'grupo', 'grupoDetalle', 'categoria', 'nombre', 'fechaHora',
          'valor', 'unidad', 'interpretacion', 'rangoReferencia', 'estado', 'notaClinica',
          'disponibleAprox']
NO_ENCONTRADO = 'Resultado no encontrado'


class BodyInvalido(Exception):
    pass


# Helper para serializar tipos de datos de DynamoDB (Decimal, etc.)
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)


def get_timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def build_response(status_code, body=None):
    response = {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json'
        }
    }
    if body is not None:
        response['body'] = json.dumps(body, cls=DecimalEncoder)
    return response


def ok(data, status_code=200):
    return build_response(status_code, {
        "success": True,
        "data": data,
        "timestamp": get_timestamp()
    })


def error(status_code, message):
    return build_response(status_code, {
        "success": False,
        "message": message,
        "timestamp": get_timestamp()
    })


def leer_body(event):
    body = event.get('body') or '{}'
    try:
        if event.get('isBase64Encoded'):
            body = base64.b64decode(body).decode('utf-8')
        # DynamoDB no acepta float: los decimales se leen como Decimal
        data = json.loads(body, parse_float=Decimal)
    except ValueError:
        raise BodyInvalido()
    if not isinstance(data, dict):
        raise BodyInvalido()
    return data


def scan_completo(**kwargs):
    items = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            return items
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def es_condicion_fallida(e):
    return e.response['Error']['Code'] == 'ConditionalCheckFailedException'


def validar(data):
    vacios = [c for c in OBLIGATORIOS if c in data and data[c] in (None, '')]
    if vacios:
        return f"Campos obligatorios vacíos: {', '.join(vacios)}"
    if 'tipo' in data and data['tipo'] not in TIPOS:
        return f"tipo debe ser uno de: {', '.join(sorted(TIPOS))}"
    if 'estado' in data and data['estado'] not in ESTADOS:
        return f"estado debe ser uno de: {', '.join(sorted(ESTADOS))}"
    return None


def listar(query):
    filtros = []
    if query.get('pacienteId'):
        filtros.append(Attr('pacienteId').eq(query['pacienteId']))
    if query.get('tipo'):
        filtros.append(Attr('tipo').eq(query['tipo']))

    kwargs = {}
    if filtros:
        kwargs['FilterExpression'] = reduce(operator.and_, filtros)
    items = scan_completo(**kwargs)
    # Más recientes primero
    items.sort(key=lambda r: r.get('fechaHora', ''), reverse=True)
    return ok({"items": items})


def crear(data):
    faltan = [c for c in OBLIGATORIOS if data.get(c) in (None, '')]
    if faltan:
        return error(400, f"Faltan campos obligatorios: {', '.join(faltan)}")
    mensaje = validar(data)
    if mensaje:
        return error(400, mensaje)

    # Se acepta un id propio (p. ej. el de Sysmedical); si no viene, se genera
    item = {KEY: str(data.get('id') or uuid.uuid4())}
    item.update({c: data[c] for c in CAMPOS if data.get(c) is not None})
    try:
        table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(#pk)',
            ExpressionAttributeNames={'#pk': KEY}
        )
    except ClientError as e:
        if es_condicion_fallida(e):
            return error(409, f"Ya existe un resultado con id {item[KEY]}")
        raise
    return ok(item, 201)


def actualizar(resultado_id, data):
    # Actualización parcial: solo cambia los campos enviados
    valores = {c: data[c] for c in CAMPOS if c in data}
    if not valores:
        return error(400, f"Envía al menos uno de: {', '.join(CAMPOS)}")
    mensaje = validar(valores)
    if mensaje:
        return error(400, mensaje)

    nombres = {'#pk': KEY}
    nombres.update({f'#{c}': c for c in valores})
    try:
        response = table.update_item(
            Key={KEY: resultado_id},
            UpdateExpression='SET ' + ', '.join(f'#{c} = :{c}' for c in valores),
            ConditionExpression='attribute_exists(#pk)',
            ExpressionAttributeNames=nombres,
            ExpressionAttributeValues={f':{c}': v for c, v in valores.items()},
            ReturnValues='ALL_NEW'
        )
    except ClientError as e:
        if es_condicion_fallida(e):
            return error(404, NO_ENCONTRADO)
        raise
    return ok(response['Attributes'])


def obtener_ruta(event):
    # routeKey de HTTP API (v2), p. ej. "GET /citas/{id}"; con rutas ANY se usa el método real
    metodo, _, plantilla = event.get('routeKey', '').partition(' ')
    if metodo == 'ANY':
        metodo = event.get('requestContext', {}).get('http', {}).get('method', '')
    return f"{metodo} {plantilla}"


def lambda_handler(event, context):
    route = obtener_ruta(event)
    resultado_id = (event.get('pathParameters') or {}).get('id')
    query = event.get('queryStringParameters') or {}

    try:
        # 1. LISTAR (GET /resultados?pacienteId=&tipo=)
        if route == 'GET /resultados':
            return listar(query)

        # 2. BUSCAR POR ID (GET /resultados/{id})
        elif route == 'GET /resultados/{id}':
            item = table.get_item(Key={KEY: resultado_id}).get('Item')
            if not item:
                return error(404, NO_ENCONTRADO)
            return ok(item)

        # 3. INSERTAR (POST /resultados)
        elif route == 'POST /resultados':
            return crear(leer_body(event))

        # 4. ACTUALIZAR (PUT /resultados/{id})
        elif route == 'PUT /resultados/{id}':
            return actualizar(resultado_id, leer_body(event))

        # 5. ELIMINAR (DELETE /resultados/{id})
        elif route == 'DELETE /resultados/{id}':
            table.delete_item(Key={KEY: resultado_id})
            return build_response(204)

        else:
            return error(400, "Ruta o método no soportado")

    except BodyInvalido:
        return error(400, "El body debe ser un objeto JSON válido")
    except Exception:
        # El detalle queda en CloudWatch; al cliente no se le exponen internos
        logger.exception("Error no controlado en %s", route)
        return error(500, "Error interno del servidor")
