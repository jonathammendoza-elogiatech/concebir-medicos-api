"""Genera los datos de ejemplo (ficticios) en seed/*.json alrededor de la fecha de hoy.

La agenda cubre desde dos semanas atrás hasta cuatro semanas adelante. El estado de cada
cita depende de la fecha y hora de generación: lo pasado queda atendido, lo futuro
confirmado. El día de la generación siempre tiene agenda completa (para hacer la demo), y
el historial de cada paciente sale de sus citas atendidas.

Uso:
    python3 scripts/generar_datos.py                          # hoy, hora actual de Lima
    python3 scripts/generar_datos.py --hoy 2026-10-05 --hora 09:00

Después: AWS_PROFILE=upc-moviles python3 scripts/cargar_datos.py --limpiar
"""
import argparse
import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONA = ZoneInfo('America/Lima')
CMP = '45782'
MEDICO = 'Dra. Ana Torres Delgado'
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'setiembre', 'octubre', 'noviembre', 'diciembre']

SLOTS_SEMANA = ['07:30', '08:15', '09:00', '09:45', '10:30', '11:15', '12:00',
                '15:00', '15:45', '16:30', '17:15', '18:00']
SLOTS_SABADO = ['08:00', '08:45', '09:30', '10:15', '11:00', '11:45', '12:30']
SLOTS_HOY_EXTRA = ['18:45', '19:30']

# id, nombre, iniciales, sexo, dni, hc, sede, nacimiento, estado, resumen, tipos de cita posibles
PACIENTES = [
    ('p01', 'Lucía Fernández Ramos', 'LF', 'Femenino', '45892147', 'HC-20481', 'SAN_ISIDRO', date(1990, 5, 14),
     'EN_TRATAMIENTO', 'FIV Ciclo 2', []),
    ('p02', 'María José Ugarte', 'MU', 'Femenino', '46120873', 'HC-19402', 'SAN_ISIDRO', date(1993, 2, 3),
     'EN_TRATAMIENTO', 'Transferencia embrionaria', ['Control post transferencia', 'Consulta de seguimiento']),
    ('p03', 'Claudia Morales Peñaloza', 'CM', 'Femenino', '41236590', 'HC-18765', 'SAN_ISIDRO', date(1986, 7, 21),
     'EN_TRATAMIENTO', 'FIV Ciclo 1 · Programada', ['Consulta de planificación FIV', 'Ecografía basal']),
    ('p04', 'Valeria Benavides Torres', 'VB', 'Femenino', '47015528', 'HC-17482', 'SAN_ISIDRO', date(1992, 11, 9),
     'EN_SEGUIMIENTO', 'Estimulación ovárica', ['Monitoreo ovulatorio', 'Ecografía transvaginal']),
    ('p05', 'Gabriela Salas Wong', 'GS', 'Femenino', '43987012', 'HC-17980', 'SAN_ISIDRO', date(1988, 4, 30),
     'EN_SEGUIMIENTO', 'Criopreservación ovocitaria', ['Consulta de criopreservación', 'Control hormonal']),
    ('p06', 'Carlos Mendoza Vargas', 'CV', 'Masculino', '42658731', 'HC-18930', 'SAN_ISIDRO', date(1987, 1, 17),
     'EN_SEGUIMIENTO', 'Estudio de factor masculino', ['Espermatograma analítico', 'Entrega de resultados']),
    ('p07', 'Rosa Paredes Salinas', 'RP', 'Femenino', '40871264', 'HC-21019', 'SAN_ISIDRO', date(1986, 3, 12),
     'EN_SEGUIMIENTO', 'Evaluación inicial de fertilidad', ['Consulta inicial fertilidad', 'Consulta de resultados']),
    ('p08', 'Fiorella Castro Medina', 'FC', 'Femenino', '72514039', 'HC-22105', 'SAN_ISIDRO', date(1995, 8, 2),
     'EN_TRATAMIENTO', 'Estimulación ovárica', ['Ecografía transvaginal', 'Control de estimulación']),
    ('p09', 'Andrea Quispe León', 'AQ', 'Femenino', '45310987', 'HC-21560', 'SAN_ISIDRO', date(1991, 6, 25),
     'EN_TRATAMIENTO', 'FIV Ciclo 1 · En estimulación', ['Control de estimulación', 'Ecografía transvaginal']),
    ('p10', 'Kiara Soto Mejía', 'KS', 'Femenino', '73145862', 'HC-22318', 'SAN_ISIDRO', date(1994, 10, 8),
     'EN_SEGUIMIENTO', 'Revisión de resultados', ['Consulta de resultados', 'Consulta de seguimiento']),
    ('p11', 'Diana Chávez Ríos', 'DC', 'Femenino', '44723618', 'HC-19877', 'LOS_OLIVOS', date(1989, 12, 1),
     'EN_TRATAMIENTO', 'Inseminación intrauterina', ['Histerosonografía', 'Control ecográfico']),
    ('p12', 'Paola Vega Castillo', 'PV', 'Femenino', '46581230', 'HC-22410', 'SAN_MIGUEL', date(1993, 9, 14),
     'EN_SEGUIMIENTO', 'Evaluación inicial de fertilidad', ['Consulta inicial fertilidad', 'Consulta de resultados']),
    ('p13', 'Milagros Huamán Torres', 'MH', 'Femenino', '42097315', 'HC-18214', 'LOS_OLIVOS', date(1987, 5, 19),
     'EN_SEGUIMIENTO', 'Control post transferencia', ['Control post transferencia', 'Prueba de embarazo (β-hCG)']),
]
POR_ID = {p[0]: p for p in PACIENTES}


def fecha_larga(f):
    return f"{f.day} de {MESES[f.month - 1]} de {f.year}"


def hora(texto):
    return time.fromisoformat(texto)


def sin_domingo(f, hacia_atras=True):
    if f.weekday() == 6:
        return f - timedelta(days=1) if hacia_atras else f + timedelta(days=1)
    return f


def duracion(tipo):
    if 'inicial' in tipo:
        return 40
    if 'Transferencia' in tipo or 'Punción' in tipo:
        return 45
    if 'planificación' in tipo or 'criopreservación' in tipo:
        return 30
    return 20


def consultorio(tipo, sede):
    if 'Transferencia' in tipo or 'Punción' in tipo:
        return 'Quirófano 1'
    if sede != 'SAN_ISIDRO':
        return 'Consultorio 2' if 'eco' in tipo.lower() or 'Histero' in tipo else 'Consultorio 1'
    return 'Consultorio 2' if 'Ecografía' in tipo else 'Consultorio 3'


def edad(nacimiento, hoy):
    return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))


def generar(hoy, ahora):
    rnd = random.Random(hoy.isoformat())  # mismo día → mismos datos
    citas = {}

    def agregar(fecha, hh, pid, tipo, sede=None, etiqueta=None, detalle=None):
        p = POR_ID[pid]
        sede = sede or p[6]
        cita = {
            "id": f"c-{fecha:%Y%m%d}-{hh.replace(':', '')}",
            "fecha": fecha.isoformat(), "hora": hh, "duracionMinutos": duracion(tipo),
            "medicoCmp": CMP, "pacienteId": pid, "pacienteNombre": p[1], "pacienteIniciales": p[2],
            "historiaClinica": p[5], "edad": edad(p[7], hoy), "sexo": p[3], "tipo": tipo,
            "sede": sede, "consultorio": consultorio(tipo, sede),
            "esProcedimientoMayor": 'Transferencia' in tipo or 'Punción' in tipo,
        }
        if etiqueta:
            cita["etiquetaTratamiento"] = etiqueta
        if detalle:
            cita["detalleConsultorio"] = detalle
        citas[(fecha, hh)] = cita

    def libre(fecha, hh, minutos):
        # Quita citas generadas que se cruzan con una cita fija del guion
        inicio = datetime.combine(fecha, hora(hh))
        for (f, h), c in list(citas.items()):
            if f != fecha:
                continue
            otra = datetime.combine(f, hora(h))
            if otra < inicio + timedelta(minutes=minutos) and inicio < otra + timedelta(minutes=c["duracionMinutos"]):
                del citas[(f, h)]

    # 1) Agenda general: dos semanas atrás hasta cuatro adelante
    desde = hoy - timedelta(days=14)
    desde -= timedelta(days=desde.weekday())
    hasta = hoy + timedelta(days=28)
    fecha = desde
    while fecha <= hasta:
        dia = fecha.weekday()
        if fecha == hoy:
            # El día de la carga siempre tiene agenda completa en San Isidro
            slots = SLOTS_SEMANA + SLOTS_HOY_EXTRA
            futuros = [s for s in slots if hora(s) >= ahora]
            pasados = [s for s in slots if hora(s) < ahora]
            elegidos = futuros[:5] + rnd.sample(pasados, min(len(pasados), 10 - min(5, len(futuros))))
        elif dia == 6:
            elegidos = []
        elif dia == 5:
            elegidos = rnd.sample(SLOTS_SABADO, 4)
        else:
            elegidos = rnd.sample(SLOTS_SEMANA, 8)
        usados = set()
        for hh in sorted(elegidos):
            tarde = hora(hh) >= time(14, 0)
            sede = 'SAN_ISIDRO'
            if fecha != hoy and tarde and dia == 1:
                sede = 'LOS_OLIVOS'
            elif fecha != hoy and tarde and dia == 3:
                sede = 'SAN_MIGUEL'
            candidatos = [p for p in PACIENTES if p[6] == sede and p[0] != 'p01' and p[0] not in usados]
            if not candidatos:
                continue
            p = rnd.choice(candidatos)
            usados.add(p[0])
            agregar(fecha, hh, p[0], rnd.choice(p[10]))
        fecha += timedelta(days=1)

    # 2) Guion fijo: tratamiento FIV de Lucía (Ciclo 2) y transferencia de María José hoy
    inicio_ciclo = hoy - timedelta(days=7)
    proximo = next((s for s in SLOTS_SEMANA + SLOTS_HOY_EXTRA
                    if hora(s) >= (datetime.combine(hoy, ahora) + timedelta(minutes=15)).time()), '10:30')
    guion = [
        (sin_domingo(hoy - timedelta(days=17)), '16:00', 'p01', 'Consulta de planificación de ciclo', None, None),
        (sin_domingo(inicio_ciclo), '11:00', 'p01', 'Inicio de estimulación ovárica', None, None),
        (sin_domingo(hoy - timedelta(days=3)), '09:15', 'p01', 'Ecografía basal y analítica hormonal', None, None),
        (hoy, proximo, 'p01', 'Control folicular · FIV', None, 'Ecografía ginecológica'),
        (sin_domingo(hoy + timedelta(days=2), hacia_atras=False), '08:15', 'p01', 'Punción ovárica (aspiración folicular)', None, None),
        (sin_domingo(hoy + timedelta(days=7), hacia_atras=False), '10:30', 'p01', 'Transferencia embrionaria', None, None),
    ]
    if proximo != '12:00':
        guion.append((hoy, '12:00', 'p02', 'Transferencia embrionaria', None, None))
    for fecha, hh, pid, tipo, sede, detalle in guion:
        # Si el paciente ya tenía otra cita ese día, se reemplaza por la del guion
        for (f, h), c in list(citas.items()):
            if f == fecha and c["pacienteId"] == pid:
                del citas[(f, h)]
        libre(fecha, hh, duracion(tipo))
        etiqueta = None
        if pid == 'p01' and fecha >= inicio_ciclo:
            etiqueta = f"Ciclo 2, Día {(fecha - inicio_ciclo).days + 1}"
        agregar(fecha, hh, pid, tipo, sede, etiqueta, detalle)

    # 3) Estados según la fecha y hora de generación
    momento = datetime.combine(hoy, ahora)
    ordenadas = sorted(citas.values(), key=lambda c: (c["fecha"], c["hora"]))
    pasadas_hoy = [c for c in ordenadas if c["fecha"] == hoy.isoformat() and datetime.combine(hoy, hora(c["hora"])) < momento]
    futuras_hoy = [c for c in ordenadas if c["fecha"] == hoy.isoformat() and c not in pasadas_hoy]
    for c in ordenadas:
        inicio = datetime.combine(date.fromisoformat(c["fecha"]), hora(c["hora"]))
        if c["pacienteId"] == 'p01':
            c["estado"] = 'ATENDIDA' if inicio < momento - timedelta(hours=1) else 'CONFIRMADA'
        elif inicio.date() < hoy:
            c["estado"] = 'ANULADA' if rnd.random() < 0.04 else 'ATENDIDA'
        elif inicio.date() > hoy:
            r = rnd.random()
            c["estado"] = 'ANULADA' if r < 0.04 else 'REPROGRAMADA' if r < 0.12 else 'CONFIRMADA'
        else:
            c["estado"] = 'ATENDIDA' if inicio < momento else 'CONFIRMADA'
    # Hoy: la última cita ya pasada queda pendiente de registro; una futura viene reprogramada
    if pasadas_hoy and pasadas_hoy[-1]["pacienteId"] != 'p01':
        pasadas_hoy[-1]["estado"] = 'CONFIRMADA'
    reprogramable = [c for c in futuras_hoy if c["pacienteId"] not in ('p01', 'p02')]
    if reprogramable:
        reprogramable[-1]["estado"] = 'REPROGRAMADA'
    for c in ordenadas:
        if c["estado"] == 'REPROGRAMADA':
            anterior = datetime.combine(date.fromisoformat(c["fecha"]), hora(c["hora"])) - timedelta(minutes=75)
            c["horaAnterior"] = anterior.strftime('%H:%M')

    # 4) Pacientes: historial = sus citas atendidas, más recientes primero
    pacientes = []
    for p in PACIENTES:
        atendidas = [c for c in reversed(ordenadas) if c["pacienteId"] == p[0] and c["estado"] == 'ATENDIDA']
        ultima = f"{atendidas[0]['fecha']}T{atendidas[0]['hora']}:00" if atendidas \
            else datetime.combine(hoy - timedelta(days=25), time(9, 0)).isoformat()
        paciente = {
            "id": p[0], "nombre": p[1], "iniciales": p[2], "edad": edad(p[7], hoy), "sexo": p[3], "dni": p[4],
            "historiaClinica": p[5], "sede": p[6], "fechaNacimiento": p[7].isoformat(),
            "telefono": f"+51 9{p[4][-2:]} 555 {p[4][:3]}",
            "correo": f"{p[1].split(' ')[0].lower()}.{p[5][-4:]}@email.com",
            "seguro": "Pacífico Salud", "plan": "Plan EPS Integral",
            "estadoTratamiento": p[8], "resumenTratamiento": p[9], "ultimaAtencion": ultima,
            "antecedentes": {"obstetricos": "G0 P0", "obstetricosDetalle": "Sin gestaciones previas.",
                             "quirurgicos": "Sin antecedentes quirúrgicos relevantes.",
                             "alergias": "No referidas", "grupoSanguineo": "O+"},
            "atenciones": [{"fecha": c["fecha"], "hora": c["hora"], "tipo": c["tipo"], "medico": MEDICO}
                           for c in atendidas[:5]],
        }
        if p[0] == 'p01':
            paciente.update({
                "telefono": "+51 987 654 321", "correo": "lucia.fernandez@email.com",
                "plan": "Plan EPS Integral (cubre procedimientos reproductivos)",
                "tratamiento": {"protocolo": "FIV", "ciclo": 2, "esquema": "Protocolo antagonista GnRH",
                                "medicacion": "Menopur 150 UI + Gonal-F 225 UI",
                                "inicio": inicio_ciclo.isoformat(), "medicoResponsable": MEDICO},
                "antecedentes": {"obstetricos": "G1 P0 A1",
                                 "obstetricosDetalle": "1 aborto espontáneo en semana 8 (2022). Sin legrados posteriores.",
                                 "quirurgicos": "Laparoscopía diagnóstica (2023) · Endometriosis grado I mínima.",
                                 "alergias": "No referidas", "grupoSanguineo": "O+"},
            })
        pacientes.append(paciente)

    # 5) Resultados de Lucía: control de hoy, basal de hace 3 días y genética pre-FIV
    basal = sin_domingo(hoy - timedelta(days=3))
    genetica = hoy - timedelta(days=12)
    toma_hoy = datetime.combine(hoy, time(7, 0)).isoformat()
    toma_basal = datetime.combine(basal, time(8, 30)).isoformat()
    toma_gen = datetime.combine(genetica, time(9, 0)).isoformat()
    d_hoy, d_basal = "Control Día 8 FIV · Protocolo antagonista", "Basal / Día 5 del ciclo estimulado"
    lh_listo = ahora >= time(12, 0)

    def res(rid, tipo, grupo, detalle, categoria, nombre, toma, valor, unidad, interp, rango, estado, **extra):
        r = {"id": rid, "pacienteId": "p01", "tipo": tipo, "grupo": grupo, "grupoDetalle": detalle,
             "categoria": categoria, "nombre": nombre, "fechaHora": toma, "valor": valor, "unidad": unidad,
             "interpretacion": interp, "rangoReferencia": rango, "estado": estado, **extra}
        return {k: v for k, v in r.items() if v is not None}

    resultados = [
        res("r1", "LABORATORIO", fecha_larga(hoy), d_hoy, "ANALÍTICA HORMONAL", "Estradiol (E2) en suero", toma_hoy,
            "1,850", "pg/mL", "Óptimo para 9 folículos", "200 - 3,000 pg/mL (estimulación)", "EN_RANGO"),
        res("r2", "LABORATORIO", fecha_larga(hoy), d_hoy, "ANALÍTICA HORMONAL", "Progesterona (P4)", toma_hoy,
            "1.65", "ng/mL", "Elevado", "< 1.00 ng/mL", "FUERA_DE_RANGO",
            notaClinica="Ligeramente elevado previo a trigger. Se sugiere evaluar congelación total (freeze-all) para evitar asincronía endometrial."),
        res("r3", "LABORATORIO", fecha_larga(hoy), d_hoy, "MARCADOR DE PICO", "Hormona luteinizante (LH)", toma_hoy,
            "3.2" if lh_listo else None, "mUI/mL", "Sin pico prematuro" if lh_listo else None,
            "< 5.0 mUI/mL (sin pico prematuro)", "EN_RANGO" if lh_listo else "PENDIENTE",
            disponibleAprox=None if lh_listo else "12:00 m."),
        res("r4", "LABORATORIO", fecha_larga(basal), d_basal, "RESERVA OVÁRICA", "Hormona antimülleriana (AMH)", toma_basal,
            "2.80", "ng/mL", "Buena reserva ovárica", "1.20 - 3.50 ng/mL", "EN_RANGO"),
        res("r5", "LABORATORIO", fecha_larga(basal), d_basal, "PERFIL TIROIDEO", "TSH (tirotropina)", toma_basal,
            "1.95", "µUI/mL", "Óptimo para fertilidad", "0.40 - 2.50 µUI/mL (objetivo preconcepción)", "EN_RANGO"),
        res("r6", "GENETICA", fecha_larga(genetica), "Estudio pre-FIV", "CITOGENÉTICA", "Cariotipo en sangre periférica",
            toma_gen, "46,XX", "", "Fórmula cromosómica normal", "46,XX / 46,XY", "EN_RANGO"),
        res("r7", "GENETICA", fecha_larga(genetica), "Estudio pre-FIV", "PORTADORES", "Panel expandido de portadores",
            toma_gen, None, "", None, "Sin variantes patogénicas", "PENDIENTE",
            disponibleAprox=(hoy + timedelta(days=5)).strftime('%d/%m/%Y')),
    ]

    medicos = [{
        "cmp": CMP, "rne": "23910", "nombre": "Ana Torres Delgado", "tratamiento": "Dra.", "apellido": "Torres",
        "iniciales": "AT", "especialidad": "Medicina reproductiva", "correo": "ana.torres@concebir.pe",
        "telefono": "+51 984 312 900", "sedeActiva": "SAN_ISIDRO", "notificacionesAgenda": True
    }]
    return {"medicos": medicos, "pacientes": pacientes, "citas": ordenadas, "resultados": resultados}


def main():
    ahora = datetime.now(ZONA)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--hoy', default=ahora.date().isoformat(), help='fecha de referencia (YYYY-MM-DD)')
    parser.add_argument('--hora', default=ahora.strftime('%H:%M'), help='hora de referencia en Lima (HH:MM)')
    parser.add_argument('--dir', default=str(Path(__file__).resolve().parent.parent / 'seed'), help='carpeta de salida')
    args = parser.parse_args()

    datos = generar(date.fromisoformat(args.hoy), time.fromisoformat(args.hora))
    salida = Path(args.dir)
    salida.mkdir(parents=True, exist_ok=True)
    for nombre, items in datos.items():
        (salida / f'{nombre}.json').write_text(json.dumps(items, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'{nombre}: {len(items)}')
    hoy = args.hoy
    del_dia = [c for c in datos["citas"] if c["fecha"] == hoy and c["estado"] != 'ANULADA']
    print(f'hoy {hoy} {args.hora}: {len(del_dia)} citas ->',
          ', '.join(f'{c["hora"]} {c["pacienteIniciales"]} {c["estado"][:4]}' for c in del_dia))


if __name__ == '__main__':
    main()
