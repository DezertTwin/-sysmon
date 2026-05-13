<div align="center">

<h1>SYSMON</h1>

**Терминальный монитор системы в реальном времени для Windows**

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Платформа-Windows%2010%2F11-0078D6?style=flat-square&logo=windows&logoColor=white)](#)
[![License](https://img.shields.io/badge/Лицензия-MIT-22c55e?style=flat-square)](LICENSE)
[![Stars](https://img.shields.io/github/stars/DezertTwin/sysmon?style=flat-square&color=f59e0b)](https://github.com/DezertTwin/sysmon/stargazers)

🇬🇧 [English documentation](README.md)

</div>

---

## Что это

**SYSMON** — терминальный дашборд, который показывает всё о вашей системе на одном экране: CPU, GPU, RAM, диски, сеть, потребление электричества, VPN и топ процессов. Построен на [Rich](https://github.com/Textualize/rich), обновляется 2 раза в секунду, практически не нагружает систему.

Цветовая тема вдохновлена тёмным интерфейсом Claude.

---

## Возможности

| Модуль | Что отображается |
|--------|-----------------|
| **CPU** | Нагрузка %, частота, температура, потребление, нагрузка по ядрам, история (sparkline) |
| **GPU** | Нагрузка %, использование VRAM, температура, потребление (NVIDIA через GPUtil или nvidia-smi) |
| **RAM** | Использование, своп, история |
| **Диски** | Занятость каждого раздела, скорость чтения/записи |
| **Сеть** | Скорость загрузки/отдачи, пинг до 8.8.8.8, потеря пакетов, встроенный speedtest |
| **Питание** | Ватты CPU + GPU + система, скользящее среднее и пик |
| **VPN** | Определение Cloudflare WARP и Zapret / GoodbyeDPI (процесс + служба) |
| **Процессы** | Топ-10 по CPU с колонкой RAM |

### Горячие клавиши

| Клавиша | Действие |
|---------|---------|
| `Q` | Выход |
| `S` | Запустить тест скорости интернета |

---

## Требования

- **Windows 10 / 11**
- **Python 3.8+**
- Для температуры и мощности CPU: запущенный [OpenHardwareMonitor](https://openhardwaremonitor.org/) или [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)

---

## Установка

```bash
# 1. Клонировать репозиторий
git clone https://github.com/DezertTwin/sysmon.git
cd sysmon

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Запустить
py sysmon.py
```

### Ярлык на рабочем столе (опционально)

```powershell
powershell -ExecutionPolicy Bypass -File install_shortcut.ps1
```

Создаёт ярлык **System Monitor** на рабочем столе — запускает дашборд в терминале.

---

## Зависимости

```
rich >= 13.0        # терминальный UI
psutil >= 5.9       # CPU / RAM / диск / сеть
GPUtil >= 1.4       # нагрузка NVIDIA GPU и VRAM  (опционально)
wmi >= 1.5.1        # аппаратные сенсоры Windows  (опционально)
speedtest-cli       # тест скорости интернета
```

Установить всё сразу:
```bash
pip install -r requirements.txt
```

---

## Температура и GPU

| Функция | Что нужно |
|---------|----------|
| Температура CPU | Запущенный OpenHardwareMonitor или LibreHardwareMonitor |
| Мощность CPU | Запущенный OpenHardwareMonitor или LibreHardwareMonitor |
| Нагрузка GPU и VRAM | Пакет `GPUtil` + драйверы NVIDIA |
| Температура GPU | OpenHardwareMonitor / LibreHardwareMonitor |
| Мощность GPU | `nvidia-smi` (входит в драйверы NVIDIA) или OHM/LHM |

Приложение работает без всего перечисленного — недоступные метрики отображаются как `—`.

---

## Определение VPN

SYSMON определяет:
- **Cloudflare WARP** — через `warp-cli status` или имя сетевого интерфейса
- **Zapret / GoodbyeDPI / winws** — через имена запущенных процессов и служб Windows

Проверка каждые **3 секунды**. Пинг до `1.1.1.1` измеряется каждые 8 секунд при активном WARP.

---

## Лицензия

MIT © 2026 [DezertTwin](https://github.com/DezertTwin)
