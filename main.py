from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime
import databases
import sqlalchemy

DATABASE_URL = "sqlite:///./servicos.db"
database = databases.Database(DATABASE_URL)
metadata = sqlalchemy.MetaData()

servicos = sqlalchemy.Table(
    "servicos",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("data", sqlalchemy.String),
    sqlalchemy.Column("local", sqlalchemy.String),
    sqlalchemy.Column("hora", sqlalchemy.String),
    sqlalchemy.Column("subcategorias", sqlalchemy.String),  
    sqlalchemy.Column("preco", sqlalchemy.Float),
    sqlalchemy.Column("realizado", sqlalchemy.Boolean, default=False),
    sqlalchemy.Column("pago", sqlalchemy.Boolean, default=False),
)

engine = sqlalchemy.create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
metadata.create_all(engine)

app = FastAPI()

class Servico(BaseModel):
    id: int
    data: str
    local: str
    hora: str
    subcategorias: List[str]
    preco: float
    realizado: bool = False
    pago: bool = False

class MensagemWhatsApp(BaseModel):
    mensagem: str

def extrair_dados_mensagem(mensagem: str) -> dict:
    try:
        linhas = mensagem.split('\n')
        data = next(l for l in linhas if 'dia' in l).split('dia')[1].split('no')[0].strip()
        local = mensagem.split('no/a ')[1].split('.')[0].strip()
        hora = mensagem.split('Horário de Entrada:')[1].split('h')[0].strip()
        sub = mensagem.split('Subcategoria(s):')[1].split(',')[0].strip()
        subcategorias = [s.strip() for s in sub.split('e') if s.strip()]
        return {
            "data": data,
            "local": local,
            "hora": hora,
            "subcategorias": subcategorias,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao extrair dados da mensagem: {str(e)}")

def calcular_preco(subcategorias: List[str], hora: str) -> float:
    sub_texto = ' '.join(subcategorias).lower()
    hora_dt = datetime.strptime(hora, "%H:%M")

    if 'montagem' in sub_texto and 'aperitivo' in sub_texto:
        return 97.0 if hora_dt.hour < 8 else 90.0
    elif 'chegada' in sub_texto and 'bolo' in sub_texto:
        return 95.0
    elif 'jantar' in sub_texto and 'fim' in sub_texto:
        return 110.0
    elif 'jantar' in sub_texto and 'bolo' in sub_texto:
        return 50.0
    else:
        return 0.0

def linha_para_servico(row) -> Servico:
    return Servico(
        id=row["id"],
        data=row["data"],
        local=row["local"],
        hora=row["hora"],
        subcategorias=row["subcategorias"].split(","),
        preco=row["preco"],
        realizado=row["realizado"],
        pago=row["pago"]
    )

@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

@app.post("/adicionar_servico")
async def adicionar_servico(dados: MensagemWhatsApp):
    info = extrair_dados_mensagem(dados.mensagem)
    preco = calcular_preco(info['subcategorias'], info['hora'])
    query = servicos.insert().values(
        data=info['data'],
        local=info['local'],
        hora=info['hora'],
        subcategorias=",".join(info['subcategorias']),
        preco=preco,
        realizado=False,
        pago=False,
    )
    last_record_id = await database.execute(query)
    return {"mensagem": "Serviço adicionado com sucesso", "id": last_record_id}

@app.get("/pendentes")
async def listar_pendentes():
    query = servicos.select().where(servicos.c.realizado == False)
    rows = await database.fetch_all(query)
    return [linha_para_servico(row) for row in rows]

@app.post("/marcar_como_realizado/{id_servico}")
async def marcar_realizado(id_servico: int):
    query = servicos.update().where(servicos.c.id == id_servico).values(realizado=True)
    result = await database.execute(query)
    if result:
        return {"mensagem": "Marcado como realizado", "id": id_servico}
    raise HTTPException(status_code=404, detail="Serviço não encontrado")

@app.get("/realizados")
async def listar_realizados():
    query = servicos.select().where(servicos.c.realizado == True)
    rows = await database.fetch_all(query)
    return [linha_para_servico(row) for row in rows]

@app.post("/marcar_como_pago/{id_servico}")
async def marcar_pago(id_servico: int):
    query = servicos.update().where(servicos.c.id == id_servico).values(pago=True)
    result = await database.execute(query)
    if result:
        return {"mensagem": "Marcado como pago", "id": id_servico}
    raise HTTPException(status_code=404, detail="Serviço não encontrado")

@app.get("/resumo")
async def resumo():
    query_pendentes = servicos.select().where(servicos.c.realizado == False)
    query_realizados = servicos.select().where(servicos.c.realizado == True)

    pendentes = await database.fetch_all(query_pendentes)
    realizados = await database.fetch_all(query_realizados)

    def total_por_mes(lista):
        totais = {}
        for s in lista:
            mes = datetime.strptime(s["data"], "%d-%m-%Y").strftime("%Y-%m")
            totais[mes] = totais.get(mes, 0) + s["preco"]
        return totais

    total_realizados = sum(s["preco"] for s in realizados)
    total_pagos = sum(s["preco"] for s in realizados if s["pago"])
    total_por_receber = total_realizados - total_pagos

    return {
        "pendentes_por_mes": total_por_mes(pendentes),
        "realizados_por_mes": total_por_mes(realizados),
        "total_realizados": total_realizados,
        "total_pagos": total_pagos,
        "total_por_receber": total_por_receber,
    }
