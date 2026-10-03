import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Inicialización de DynamoDB
dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME', 'pacientes')
table = dynamodb.Table(TABLE_NAME)

KEY = 'id'
SEDES = {'SAN_ISIDRO', 'LOS_OLIVOS', 'SAN_MIGUEL'}
ESTADOS_TRATAMIENTO = {'EN_TRATAMIENTO', 'EN_SEGUIMIENTO'}
OBLIGATORIOS = ['nombre', 'dni', 'historiaClinica', 'sede']
# tratamiento y antecedentes son objetos; atenciones es una lista de objetos
CAMPOS = ['nombre', 'iniciales', 'edad', 'sexo', 'dni', 'historiaClinica', 'sede',
          'fechaNacimiento', 'telefono', 'correo', 'seguro', 'plan', 'estadoTratamiento',
          'resumenTratamiento', 'tratamiento', 'ultimaAtencion', 'antecedentes', 'atenciones']
NO_ENCONTRADO = 'Paciente no encontrado'


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
    if 'sede' in data and data['sede'] not in SEDES:
        return f"sede debe ser uno de: {', '.join(sorted(SEDES))}"
    if data.get('estadoTratamiento') is not None and data['estadoTratamiento'] not in ESTADOS_TRATAMIENTO:
        return f"estadoTratamiento debe ser uno de: {', '.join(sorted(ESTADOS_TRATAMIENTO))}"
    return None


def listar(query):
    kwargs = {}
    if query.get('sede'):
        kwargs['FilterExpression'] = Attr('sede').eq(query['sede'])
    items = scan_completo(**kwargs)

    # Búsqueda por nombre, historia clínica o DNI (?q=)
    texto = (query.get('q') or '').strip().lower()
    if texto:
        items = [p for p in items
                 if texto in str(p.get('nombre', '')).lower()
                 or texto in str(p.get('historiaClinica', '')).lower()
                 or texto in str(p.get('dni', ''))]

    items.sort(key=lambda p: p.get('nombre', ''))
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
            return error(409, f"Ya existe un paciente con id {item[KEY]}")
        raise
    return ok(item, 201)


def actualizar(paciente_id, data):
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
            Key={KEY: paciente_id},
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
    paciente_id = (event.get('pathParameters') or {}).get('id')
    query = event.get('queryStringParameters') or {}

    try:
        # 1. LISTAR / BUSCAR (GET /pacientes?sede=&q=)
        if route == 'GET /pacientes':
            return listar(query)

        # 2. BUSCAR POR ID (GET /pacientes/{id})
        elif route == 'GET /pacientes/{id}':
            item = table.get_item(Key={KEY: paciente_id}).get('Item')
            if not item:
                return error(404, NO_ENCONTRADO)
            return ok(item)

        # 3. INSERTAR (POST /pacientes)
        elif route == 'POST /pacientes':
            return crear(leer_body(event))

        # 4. ACTUALIZAR (PUT /pacientes/{id})
        elif route == 'PUT /pacientes/{id}':
            return actualizar(paciente_id, leer_body(event))

        # 5. ELIMINAR (DELETE /pacientes/{id})
        elif route == 'DELETE /pacientes/{id}':
            table.delete_item(Key={KEY: paciente_id})
            return build_response(204)

        else:
            return error(400, "Ruta o método no soportado")

    except BodyInvalido:
        return error(400, "El body debe ser un objeto JSON válido")
    except Exception:
        # El detalle queda en CloudWatch; al cliente no se le exponen internos
        logger.exception("Error no controlado en %s", route)
        return error(500, "Error interno del servidor")
