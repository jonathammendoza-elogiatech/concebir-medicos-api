# concebir-medicos-api

Servicio REST de **Concebir Médicos** (E6): capa en la nube que reemplaza al mock de
Sysmedical de la app Android. AWS Lambda (Python) + DynamoDB + API Gateway (HTTP API),
con login de médicos en **Amazon Cognito**.

> Todos los datos de `seed/` son **ficticios** (mismos pacientes y médica del prototipo).

```
App Android ──(CMP + contraseña)──▶ Cognito User Pool ──▶ access token (JWT)
     │
     └──(Authorization: Bearer <token>)──▶ API Gateway (HTTP API + autorizador JWT)
                                               └──▶ Lambda ──▶ DynamoDB
```

## Despliegue actual (Learner Lab · us-east-1)

| Recurso | Valor |
| --- | --- |
| API (base URL) | `https://0pn0osiqr1.execute-api.us-east-1.amazonaws.com` |
| Cognito User Pool ID | `us-east-1_C3lXszYnI` |
| Cognito Client ID (público, sin secreto) | `2b3m67nub9tnakm6ifiac16mje` |
| Usuario demo | CMP `45782` (la contraseña se comparte por el canal del equipo; no se versiona) |
| Lambdas | `medicos`, `pacientes`, `citas`, `resultados` (Python 3.13, `LabRole`) |

Tokens: access e ID de 60 min, refresh de 30 días.

## Contenido

| Ruta | Qué es |
| --- | --- |
| `lambdas/medicos.py` | Perfil del médico logueado (`/medicos/me`) + CRUD de médicos |
| `lambdas/pacientes.py` | CRUD de pacientes + búsqueda (`?q=`, `?sede=`) |
| `lambdas/citas.py` | Agenda (`?fecha=`, `?desde=&hasta=`, `?sede=`, `?estado=`), CRUD y registro de atención |
| `lambdas/resultados.py` | Resultados de laboratorio y genética (`?pacienteId=`, `?tipo=`) + CRUD |
| `seed/*.json` | Datos de ejemplo: Dra. Ana Torres (CMP 45782), 13 pacientes, ~290 citas, 7 resultados |
| `scripts/generar_datos.py` | Genera `seed/` con la agenda alrededor de la fecha de hoy |
| `scripts/cargar_datos.py` | Carga `seed/` en DynamoDB (`--limpiar` borra antes lo que haya) |
| `postman/ConcebirMedicos.postman_collection.json` | Colección Postman v2.1 con login Cognito y todas las rutas |

## 1. DynamoDB — tablas

Consola → DynamoDB → **Create table** (capacidad *On-demand*):

| Tabla | Partition key (String) |
| --- | --- |
| `medicos` | `cmp` |
| `pacientes` | `id` |
| `citas` | `id` |
| `resultados` | `id` |

## 2. Cognito — login con CMP

1. Consola → Cognito → **Create user pool**.
   - Tipo de aplicación: **Mobile app** · nombre: `concebir-medicos-app`.
   - Identificador de inicio de sesión: **Username** (el usuario es el CMP, no el correo).
   - **Sin auto-registro** (*self-registration* desactivado): los médicos los da de alta la clínica.
2. En el *App client* creado → **Edit**:
   - *Client secret*: **ninguno** (cliente público; una app móvil no puede guardar secretos).
   - *Authentication flows*: `ALLOW_USER_PASSWORD_AUTH` y `ALLOW_REFRESH_TOKEN_AUTH`.
3. **Users → Create user**: username `45782` y una contraseña de al menos 8 caracteres
   (la política por defecto pide mayúscula, minúscula, número y símbolo).
4. El usuario queda en `FORCE_CHANGE_PASSWORD`. Para dejar la contraseña como definitiva,
   desde la terminal del Learner Lab:

   ```bash
   aws cognito-idp admin-set-user-password --user-pool-id <USER_POOL_ID> \
     --username 45782 --password '<CONTRASEÑA>' --permanent
   ```

Anota el **User Pool ID** (`us-east-1_XXXX`) y el **Client ID**.

## 3. Lambdas

Por cada archivo de `lambdas/` (4 funciones: `medicos`, `pacientes`, `citas`, `resultados`):

1. Lambda → **Create function** → *Author from scratch* · runtime **Python 3.13**.
2. *Execution role* → **Use an existing role** → **`LabRole`**
   (el Learner Lab no permite crear roles).
3. Pega el contenido del archivo en `lambda_function.py` → **Deploy**.
4. *Configuration → Environment variables*: `TABLE_NAME` = nombre de la tabla.

Para probar en la consola, evento de ejemplo (*Test*):

```json
{
  "routeKey": "GET /citas",
  "queryStringParameters": { "fecha": "2026-09-24" },
  "requestContext": {
    "http": { "method": "GET" },
    "authorizer": { "jwt": { "claims": { "username": "45782" } } }
  }
}
```

## 4. API Gateway — HTTP API

1. API Gateway → **Create API → HTTP API** · nombre `concebir-medicos-api` · stage `$default` (auto-deploy).
2. **Routes** (cada una integrada con su Lambda):

   | Ruta | Lambda |
   | --- | --- |
   | `ANY /medicos` · `ANY /medicos/me` · `ANY /medicos/{cmp}` | `medicos` |
   | `ANY /pacientes` · `ANY /pacientes/{id}` | `pacientes` |
   | `ANY /citas` · `ANY /citas/{id}` · `POST /citas/{id}/atencion` | `citas` |
   | `ANY /resultados` · `ANY /resultados/{id}` | `resultados` |

   Las Lambdas aceptan tanto rutas `ANY` como rutas por método (`GET /citas`, etc.).
3. **Authorization → Create authorizer → JWT**, y adjúntalo a **todas** las rutas:
   - *Identity source*: `$request.header.Authorization`
   - *Issuer URL*: `https://cognito-idp.us-east-1.amazonaws.com/<USER_POOL_ID>`
   - *Audience*: `<CLIENT_ID>`

## 5. Cargar los datos de ejemplo

Con las credenciales del Learner Lab (*AWS Details → AWS CLI → Show*) copiadas en
`~/.aws/credentials` bajo un perfil `[upc-moviles]` (caducan al cerrar el lab):

```bash
pip install boto3
python3 scripts/generar_datos.py                                   # agenda alrededor de hoy
AWS_PROFILE=upc-moviles python3 scripts/cargar_datos.py --limpiar  # deja solo los datos de ejemplo
```

La app usa la fecha y hora reales (Lima), así que la agenda se genera alrededor del día en que
se corre el script:

- Cubre desde dos semanas atrás hasta cuatro semanas adelante (lunes a sábado). El día de la
  generación siempre tiene agenda completa en San Isidro, para poder hacer la demo.
- El estado depende de la hora de generación: lo pasado queda **atendido** (la última cita de hoy,
  **pendiente de registro**) y lo futuro **confirmado** (algunas reprogramadas o anuladas).
- Guion fijo: tratamiento FIV de Lucía Fernández (ciclo 2, hoy es el día 8, punción en 2 días y
  transferencia en 7) y una transferencia de María José hoy al mediodía.
- El historial de cada paciente sale de sus citas atendidas.

**Antes de una demo, vuelve a generar y cargar** para que "hoy" y las horas calcen. Con la misma
fecha, el script produce los mismos datos (`--hoy` y `--hora` permiten fijarlos).

## 6. Probar

```bash
# 1) Login: obtener el access token
curl -s -X POST https://cognito-idp.us-east-1.amazonaws.com/ \
  -H 'Content-Type: application/x-amz-json-1.1' \
  -H 'X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth' \
  -d '{"AuthFlow":"USER_PASSWORD_AUTH","ClientId":"<CLIENT_ID>",
       "AuthParameters":{"USERNAME":"45782","PASSWORD":"<CONTRASEÑA>"}}'

# 2) Llamar al API con AuthenticationResult.AccessToken
API=https://<api-id>.execute-api.us-east-1.amazonaws.com
curl -H "Authorization: Bearer $TOKEN" "$API/medicos/me"
curl -H "Authorization: Bearer $TOKEN" "$API/citas?fecha=2026-09-24"
```

### Con Postman

Importa `postman/ConcebirMedicos.postman_collection.json`, completa la variable de colección
`password` y ejecuta **0. Auth → Login**: el `accessToken` queda guardado y el resto de requests
lo envían como `Bearer`. Las carpetas de CRUD crean un registro de prueba, lo actualizan y lo
eliminan, sin tocar los datos de ejemplo. También corre por consola:

```bash
newman run postman/ConcebirMedicos.postman_collection.json --env-var 'password=<CONTRASEÑA>'
```

## Rutas

| Método y ruta | Uso en la app |
| --- | --- |
| `GET /medicos/me` | Perfil del médico logueado (E1) |
| `PATCH /medicos/me` | Cambiar `sedeActiva` / `notificacionesAgenda` (E1) |
| `GET /citas?fecha=YYYY-MM-DD` | Agenda del día; `?desde=&hasta=` para la semana (E2) |
| `GET /citas/{id}` | Detalle de cita (E3) |
| `POST /citas/{id}/atencion` | Nota firmada: `{"tipoNota": "EVOLUCION", "nota": "...", "marcarAtendida": true}`; también la agrega al historial del paciente (E3) |
| `GET /pacientes?q=&sede=` | Búsqueda de pacientes (E4) |
| `GET /pacientes/{id}` | Ficha con tratamiento, antecedentes y atenciones (E4) |
| `GET /resultados?pacienteId=&tipo=` | Resultados de laboratorio / genética (E4) |
| `GET`, `POST`, `PUT`, `DELETE` en `/medicos`, `/pacientes`, `/citas`, `/resultados` | CRUD para administración y pruebas |

Respuesta estándar (igual que en clase):

```json
{ "success": true, "data": { "items": [ ... ] }, "timestamp": "2026-09-24T15:15:00.000Z" }
{ "success": false, "message": "Cita no encontrada", "timestamp": "..." }
```

Formatos: fechas `YYYY-MM-DD`, horas `HH:MM`, fecha-hora `YYYY-MM-DDTHH:MM:SS`; sedes y
estados con el nombre del enum de la app (`SAN_ISIDRO`, `CONFIRMADA`, `EN_RANGO`, ...).

## Decisiones

- **El CMP sale del token, no del body.** `GET /citas` devuelve solo la agenda del médico
  logueado, y `POST /citas/{id}/atencion` guarda `firmadoPor` (CMP) y la hora del servidor
  (no repudio). La firma biométrica se hace en la app antes de llamar.
- **Registrar una atención actualiza la ficha**: la Lambda `citas` agrega la atención al
  historial del paciente (`atenciones`, `ultimaAtencion`), como lo haría Sysmedical. Lee las
  tablas `pacientes` y `medicos` (variables opcionales `PACIENTES_TABLE` y `MEDICOS_TABLE`).
- **`PUT` es parcial**: actualiza solo los campos enviados (no borra el resto ni las notas de
  la cita). `POST` acepta un `id` propio (p. ej. el de Sysmedical) y responde `409` si ya existe.
- **Validación**: campos obligatorios, enums y formatos de fecha/hora → `400`. Los errores
  internos se registran en CloudWatch y al cliente solo le llega `Error interno del servidor`.
- **`scan` + filtro** es suficiente para el volumen del piloto. Si crece: GSI por
  `medicoCmp + fecha` (citas) y por `pacienteId` (resultados).
- **Pendiente para producción**: restringir el CRUD de `/medicos` a un grupo `admin` de
  Cognito (`cognito:groups`) y validar en `GET /citas/{id}` que la cita sea del médico.

## Desde la app (E1 — login y sesión)

- **Login**: `InitiateAuth` con `USER_PASSWORD_AUTH` (la misma llamada del paso 6, vía
  Retrofit; no hace falta el SDK de Amplify). Guarda `AccessToken`, `RefreshToken` y `ExpiresIn`.
- **Biometría**: el `RefreshToken` se guarda cifrado con una llave del Android Keystore que
  exige huella/rostro. Al entrar con biometría se descifra y se renueva el token con
  `REFRESH_TOKEN_AUTH`.
- Cada llamada al API lleva `Authorization: Bearer <AccessToken>` (un interceptor de OkHttp).
