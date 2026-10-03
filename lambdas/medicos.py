import base64
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Inicialización de DynamoDB
dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME', 'medicos')
table = dynamodb.Table(TABLE_NAME)

# El CMP es la clave de la tabla y también el usuario en Cognito
KEY = 'cmp'
SEDES = {'SAN_ISIDRO', 'LOS_OLIVOS', 'SAN_MIGUEL'}
OBLIGATORIOS = ['nombre', 'tratamiento', 'apellido', 'especialidad', 'sedeActiva']
CAMPOS = ['rne', 'nombre', 'tratamiento', 'apellido', 'iniciales', 'especialidad',
          'correo', 'telefono', 'sedeActiva', 'notificacionesAgenda']
# Lo único que el médico cambia de su propio perfil desde la app
CAMPOS_PERFIL = ['sedeActiva', 'notificacionesAgenda']
NO_ENCONTRADO = 'Médico no encontrado'


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


def cmp_autenticado(event):
    # Claims del token de Cognito, validado por el autorizador JWT de API Gateway
    claims = event.get('requestContext', {}).get('authorizer', {}).get('jwt', {}).get('claims', {})
    return claims.get('cognito:username') or claims.get('username')


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
    if 'sedeActiva' in data and data['sedeActiva'] not in SEDES:
        return f"sedeActiva debe ser uno de: {', '.join(sorted(SEDES))}"
    if 'notificacionesAgenda' in data and not isinstance(data['notificacionesAgenda'], bool):
        return "notificacionesAgenda debe ser true o false"
    return None


def obtener(cmp):
    item = table.get_item(Key={KEY: cmp}).get('Item')
    if not item:
        return error(404, NO_ENCONTRADO)
    return ok(item)


def crear(data):
    faltan = [c for c in [KEY] + OBLIGATORIOS if data.get(c) in (None, '')]
    if faltan:
        return error(400, f"Faltan campos obligatorios: {', '.join(faltan)}")
    mensaje = validar(data)
    if mensaje:
        return error(400, mensaje)

    item = {KEY: str(data[KEY]), 'notificacionesAgenda': True}
    item.update({c: data[c] for c in CAMPOS if data.get(c) is not None})
    try:
        table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(#pk)',
            ExpressionAttributeNames={'#pk': KEY}
        )
    except ClientError as e:
        if es_condicion_fallida(e):
            return error(409, f"Ya existe un médico con CMP {item[KEY]}")
        raise
    return ok(item, 201)


def actualizar(cmp, data, campos):
    # Actualización parcial: solo cambia los campos enviados
    valores = {c: data[c] for c in campos if c in data}
    if not valores:
        return error(400, f"Envía al menos uno de: {', '.join(campos)}")
    mensaje = validar(valores)
    if mensaje:
        return error(400, mensaje)

    nombres = {'#pk': KEY}
    nombres.update({f'#{c}': c for c in valores})
    try:
        response = table.update_item(
            Key={KEY: cmp},
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
    cmp = (event.get('pathParameters') or {}).get('cmp')

    try:
        # 1. PERFIL DEL MÉDICO LOGUEADO (GET /medicos/me)
        if route == 'GET /medicos/me':
            cmp_sesion = cmp_autenticado(event)
            if not cmp_sesion:
                return error(401, "No autenticado")
            return obtener(cmp_sesion)

        # 2. CAMBIAR SEDE ACTIVA / NOTIFICACIONES (PATCH /medicos/me)
        elif route == 'PATCH /medicos/me':
            cmp_sesion = cmp_autenticado(event)
            if not cmp_sesion:
                return error(401, "No autenticado")
            return actualizar(cmp_sesion, leer_body(event), CAMPOS_PERFIL)

        # 3. LISTAR (GET /medicos)
        elif route == 'GET /medicos':
            return ok({"items": scan_completo()})

        # 4. BUSCAR POR CMP (GET /medicos/{cmp})
        elif route == 'GET /medicos/{cmp}':
            return obtener(cmp)

        # 5. INSERTAR (POST /medicos)
        elif route == 'POST /medicos':
            return crear(leer_body(event))

        # 6. ACTUALIZAR (PUT /medicos/{cmp})
        elif route == 'PUT /medicos/{cmp}':
            return actualizar(cmp, leer_body(event), CAMPOS)

        # 7. ELIMINAR (DELETE /medicos/{cmp})
        elif route == 'DELETE /medicos/{cmp}':
            table.delete_item(Key={KEY: cmp})
            return build_response(204)

        else:
            return error(400, "Ruta o método no soportado")

    except BodyInvalido:
        return error(400, "El body debe ser un objeto JSON válido")
    except Exception:
        # El detalle queda en CloudWatch; al cliente no se le exponen internos
        logger.exception("Error no controlado en %s", route)
        return error(500, "Error interno del servidor")
