import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from src.news_aggregator.db.models import Item
from src.news_aggregator.config import get_settings

settings = get_settings()
engine = create_engine(settings.resolved_database_url)
with Session(engine) as session:
    item_id = int(sys.argv[1])
    signal = 1 if sys.argv[2] == "up" else -1
    item = session.get(Item, item_id)
    if item:
        item.feedback = signal
        session.commit()
        print(f"已經透過資料庫手動將「{item.title_zh or item.title}」這則新聞的 {'👍' if signal > 0 else '👎'} 回饋記錄下來了。")
    else:
        print(f"找不到 ID: {item_id}")
