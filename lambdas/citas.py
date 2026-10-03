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
TABLE_NAME = os.environ.get('TABLE_NAME', 'citas')
table = dynamodb.Table(TABLE_NAME)
# Al registrar una atención también se actualiza el historial del paciente
pacientes_table = dynamodb.Table(os.environ.get('PACIENTES_TABLE', 'pacientes'))
medicos_table = dynamodb.Table(os.environ.get('MEDICOS_TABLE', 'medicos'))

KEY = 'id'
SEDES = {'SAN_ISIDRO', 'LOS_OLIVOS', 'SAN_MIGUEL'}
ESTADOS = {'CONFIRMADA', 'ATENDIDA', 'REPROGRAMADA', 'ANULADA'}
TIPOS_NOTA = {'EVOLUCION', 'INDICACION', 'OBSERVACION'}
OBLIGATORIOS = ['fecha', 'hora', 'pacienteId', 'tipo', 'sede', 'consultorio']
# Los datos del paciente van copiados en la cita para pintar la agenda sin otra consulta
CAMPOS = ['fecha', 'hora', 'duracionMinutos', 'medicoCmp', 'pacienteId', 'pacienteNombre',
          'pacienteIniciales', 'historiaClinica', 'edad', 'sexo', 'tipo', 'etiquetaTratamiento',
          'sede', 'consultorio', 'detalleConsultorio', 'estado', 'horaAnterior',
          'esProcedimientoMayor']
NO_ENCONTRADO = 'Cita no encontrada'


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


def formato_valido(valor, formato):
    try:
        datetime.strptime(str(valor), formato)
        return True
    except ValueError:
        return False


def validar(data):
    vacios = [c for c in OBLIGATORIOS if c in data and data[c] in (None, '')]
    if vacios:
        return f"Campos obligatorios vacíos: {', '.join(vacios)}"
    if 'fecha' in data and not formato_valido(data['fecha'], '%Y-%m-%d'):
        return "fecha debe tener formato YYYY-MM-DD"
    for campo in ('hora', 'horaAnterior'):
        if data.get(campo) is not None and not formato_valido(data[campo], '%H:%M'):
            return f"{campo} debe tener formato HH:MM"
    if 'sede' in data and data['sede'] not in SEDES:
        return f"sede debe ser uno de: {', '.join(sorted(SEDES))}"
    if 'estado' in data and data['estado'] not in ESTADOS:
        return f"estado debe ser uno de: {', '.join(sorted(ESTADOS))}"
    return None


def listar(event, query):
    filtros = []
    # Cada médico ve solo su agenda
    cmp = cmp_autenticado(event)
    if cmp:
        filtros.append(Attr('medicoCmp').eq(cmp))
    # Día (?fecha=) o semana (?desde=&hasta=), en formato YYYY-MM-DD
    if query.get('fecha'):
        filtros.append(Attr('fecha').eq(query['fecha']))
    if query.get('desde'):
        filtros.append(Attr('fecha').gte(query['desde']))
    if query.get('hasta'):
        filtros.append(Attr('fecha').lte(query['hasta']))
    if query.get('sede'):
        filtros.append(Attr('sede').eq(query['sede']))
    if query.get('estado'):
        filtros.append(Attr('estado').eq(query['estado']))

    kwargs = {}
    if filtros:
        kwargs['FilterExpression'] = reduce(operator.and_, filtros)
    items = scan_completo(**kwargs)
    items.sort(key=lambda c: (c.get('fecha', ''), c.get('hora', '')))
    return ok({"items": items})


def crear(event, data):
    faltan = [c for c in OBLIGATORIOS if data.get(c) in (None, '')]
    if faltan:
        return error(400, f"Faltan campos obligatorios: {', '.join(faltan)}")
    mensaje = validar(data)
    if mensaje:
        return error(400, mensaje)

    # Se acepta un id propio (p. ej. el de Sysmedical); si no viene, se genera
    item = {
        KEY: str(data.get('id') or uuid.uuid4()),
        'estado': 'CONFIRMADA',
        'duracionMinutos': 20,
        'esProcedimientoMayor': False
    }
    item.update({c: data[c] for c in CAMPOS if data.get(c) is not None})
    # Si no se indica el médico, la cita es del médico logueado
    item.setdefault('medicoCmp', cmp_autenticado(event))
    if item['medicoCmp'] is None:
        return error(400, "Falta medicoCmp")
    try:
        table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(#pk)',
            ExpressionAttributeNames={'#pk': KEY}
        )
    except ClientError as e:
        if es_condicion_fallida(e):
            return error(409, f"Ya existe una cita con id {item[KEY]}")
        raise
    return ok(item, 201)


def actualizar(cita_id, data):
    # Actualización parcial: solo cambia los campos enviados (las notas no se tocan aquí)
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
            Key={KEY: cita_id},
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


def agregar_al_historial(cita, cmp):
    # Como haría Sysmedical: la atención aparece en el historial de la ficha del paciente
    medico = medicos_table.get_item(Key={'cmp': cmp}).get('Item') or {}
    nombre_medico = f"{medico.get('tratamiento', '')} {medico.get('nombre', '')}".strip() or cmp
    atencion = {
        "fecha": cita['fecha'],
        "hora": cita['hora'],
        "tipo": cita.get('tipo', ''),
        "medico": nombre_medico
    }
    try:
        pacientes_table.update_item(
            Key={'id': cita['pacienteId']},
            UpdateExpression='SET #atenciones = list_append(:nueva, if_not_exists(#atenciones, :vacia)), '
                             '#ultima = :ultima',
            ConditionExpression='attribute_exists(#pk)',
            ExpressionAttributeNames={'#pk': 'id', '#atenciones': 'atenciones', '#ultima': 'ultimaAtencion'},
            ExpressionAttributeValues={
                ':nueva': [atencion],
                ':vacia': [],
                ':ultima': f"{cita['fecha']}T{cita['hora']}:00"
            }
        )
    except ClientError as e:
        if not es_condicion_fallida(e):
            raise
        logger.warning("La cita %s apunta a un paciente inexistente", cita.get('id'))


def registrar_atencion(event, cita_id, data):
    # La firma biométrica ocurre en la app; aquí queda quién firmó (según el token) y cuándo
    cmp = cmp_autenticado(event)
    if not cmp:
        return error(401, "No autenticado")
    tipo_nota = data.get('tipoNota')
    if tipo_nota not in TIPOS_NOTA:
        return error(400, f"tipoNota debe ser uno de: {', '.join(sorted(TIPOS_NOTA))}")
    nota = str(data.get('nota') or '').strip()
    if not nota:
        return error(400, "La nota no puede estar vacía")
    marcar_atendida = data.get('marcarAtendida', False)
    if not isinstance(marcar_atendida, bool):
        return error(400, "marcarAtendida debe ser true o false")

    registro = {
        "tipoNota": tipo_nota,
        "nota": nota,
        "firmadoPor": cmp,
        "fechaHora": get_timestamp()
    }
    expresion = 'SET #notas = list_append(if_not_exists(#notas, :vacia), :nueva)'
    nombres = {'#pk': KEY, '#notas': 'notas'}
    valores = {':vacia': [], ':nueva': [registro]}
    if marcar_atendida:
        expresion += ', #estado = :atendida'
        nombres['#estado'] = 'estado'
        valores[':atendida'] = 'ATENDIDA'

    try:
        response = table.update_item(
            Key={KEY: cita_id},
            UpdateExpression=expresion,
            ConditionExpression='attribute_exists(#pk)',
            ExpressionAttributeNames=nombres,
            ExpressionAttributeValues=valores,
            ReturnValues='ALL_NEW'
        )
    except ClientError as e:
        if es_condicion_fallida(e):
            return error(404, NO_ENCONTRADO)
        raise
    cita = response['Attributes']
    agregar_al_historial(cita, cmp)
    return ok(cita, 201)


def obtener_ruta(event):
    # routeKey de HTTP API (v2), p. ej. "GET /citas/{id}"; con rutas ANY se usa el método real
    metodo, _, plantilla = event.get('routeKey', '').partition(' ')
    if metodo == 'ANY':
        metodo = event.get('requestContext', {}).get('http', {}).get('method', '')
    return f"{metodo} {plantilla}"


def lambda_handler(event, context):
    route = obtener_ruta(event)
    cita_id = (event.get('pathParameters') or {}).get('id')
    query = event.get('queryStringParameters') or {}

    try:
        # 1. AGENDA (GET /citas?fecha=&desde=&hasta=&sede=&estado=)
        if route == 'GET /citas':
            return listar(event, query)

        # 2. DETALLE (GET /citas/{id})
        elif route == 'GET /citas/{id}':
            item = table.get_item(Key={KEY: cita_id}).get('Item')
            if not item:
                return error(404, NO_ENCONTRADO)
            return ok(item)

        # 3. INSERTAR (POST /citas)
        elif route == 'POST /citas':
            return crear(event, leer_body(event))

        # 4. ACTUALIZAR (PUT /citas/{id})
        elif route == 'PUT /citas/{id}':
            return actualizar(cita_id, leer_body(event))

        # 5. REGISTRAR ATENCIÓN (POST /citas/{id}/atencion)
        elif route == 'POST /citas/{id}/atencion':
            return registrar_atencion(event, cita_id, leer_body(event))

        # 6. ELIMINAR (DELETE /citas/{id})
        elif route == 'DELETE /citas/{id}':
            table.delete_item(Key={KEY: cita_id})
            return build_response(204)

        else:
            return error(400, "Ruta o método no soportado")

    except BodyInvalido:
        return error(400, "El body debe ser un objeto JSON válido")
    except Exception:
        # El detalle queda en CloudWatch; al cliente no se le exponen internos
        logger.exception("Error no controlado en %s", route)
        return error(500, "Error interno del servidor")
