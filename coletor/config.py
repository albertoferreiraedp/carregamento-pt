"""Configuração central do coletor."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

# Feeds públicos NAP/MOBI.E (DATEX II)
STATUS_URL = os.environ.get("STATUS_URL", "https://pgm.mobie.pt/integration/nap/evActualStatus")
INFRA_URL = os.environ.get("INFRA_URL", "https://pgm.mobie.pt/integration/nap/evChargingInfra")

TZ = ZoneInfo("Europe/Lisbon")

STATE_DIR = Path(os.environ.get("STATE_DIR", "state"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))

# Recolhas mais próximas do que isto são ignoradas (evita duplicados
# quando o cron-job.org e o schedule do GitHub disparam em simultâneo).
MIN_INTERVAL_S = 150

# Periodicidade de atualização do inventário estático.
STATIC_REFRESH_H = 6

# Um dia só é arquivado depois das 00:10 (Lisboa) do dia seguinte.
ARCHIVE_GRACE_MIN = 10

# Se o feed trouxer menos do que esta fração das tomadas conhecidas,
# é tratado como falha do feed (evita marcar milhares de tomadas como ausentes).
MIN_FEED_RATIO = 0.5

# Estado registado quando uma tomada deixa de constar do feed dinâmico.
ABSENT = "__AUSENTE__"

# Pedidos condicionais (If-None-Match / If-Modified-Since). Desligados durante a
# medição: só serão ativados se os cabeçalhos do servidor se provarem fiáveis.
USE_CONDITIONAL = False

# Cópia bruta diária do XML (para reprocessamento) só durante o piloto.
RAW_DAILY_UNTIL = "2026-10-07"

USER_AGENT = "disponibilidade-carregamento-pt/1.0 (projeto independente; fonte NAP/MOBI.E)"
HTTP_TIMEOUT = (15, 120)
HTTP_RETRIES = 3
