"""Telegram-only downloader; identifiers are accepted only by the handler."""

from pathlib import Path
import os

class TelegramFileDownloader:
    def __init__(self, bot, storage_path: Path): self.bot=bot; self.storage_path=Path(storage_path)
    async def download(self,file_id:str,destination:Path):
        resolved=destination.resolve(); root=self.storage_path.resolve()
        if root not in resolved.parents: raise ValueError("destination outside document storage")
        telegram_file=await self.bot.get_file(file_id)
        await telegram_file.download_to_drive(custom_path=str(resolved))
        os.chmod(resolved,0o600)
        return resolved
