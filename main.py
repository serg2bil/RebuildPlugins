import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
import json
import zipfile
import tempfile
import threading
import shutil
from pathlib import Path
import logging
import re
import glob
import datetime
import sys

# Глобальная переменная для хранения переводов
translations = {}

def load_language(lang_code):
    """
    Загружает файл перевода для указанного языка.
    
    :param lang_code: Код языка (например, 'ru', 'en').
    """
    global translations
    try:
        with open(f"locales/{lang_code}.json", 'r', encoding='utf-8') as f:
            translations = json.load(f)
    except FileNotFoundError:
        messagebox.showerror("Ошибка", f"Файл перевода для языка '{lang_code}' не найден.")
    except json.JSONDecodeError:
        messagebox.showerror("Ошибка", f"Файл перевода для языка '{lang_code}' содержит неверный JSON.")

def translate(key):
    """
    Возвращает переведенную строку по ключу.
    
    :param key: Ключ строки в файле перевода.
    :return: Переведенная строка или ключ, если перевод не найден.
    """
    return translations.get(key, key)

# Загрузка перевода по умолчанию
current_language = "en"
load_language(current_language)

# Создание папки "logs" и "logs/archives" если они не существуют
LOGS_FOLDER = "logs"
ARCHIVES_FOLDER = os.path.join(LOGS_FOLDER, "archives")

for folder in [LOGS_FOLDER, ARCHIVES_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Определение пути для папки RebuiltPlugins
REBUILT_PLUGINS_FOLDER = Path(__file__).parent / "RebuiltPlugins"

# Настройка логирования
logger = logging.getLogger("PluginBuilder")
logger.setLevel(logging.DEBUG)  # Ловим все уровни логирования

# Создаем форматтер, включающий category
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(category)s - %(message)s')

# Фильтры для категорий 'builder' и 'system'
class BuilderFilter(logging.Filter):
    def filter(self, record):
        return getattr(record, 'category', '') == "builder"

class SystemFilter(logging.Filter):
    def filter(self, record):
        return getattr(record, 'category', '') == "system"

def setup_file_handler(log_path, level=logging.DEBUG, category_filter=None):
    """
    Настраивает FileHandler без ротации.
    
    :param log_path: Путь к лог-файлу.
    :param level: Уровень логирования.
    :param category_filter: Фильтр для категории логов.
    :return: Настроенный обработчик.
    """
    handler = logging.FileHandler(log_path, encoding='utf-8')
    handler.setLevel(level)
    handler.setFormatter(formatter)
    
    if category_filter:
        handler.addFilter(category_filter)
    
    return handler

# Создание обработчиков логов без ротации
general_log_path = os.path.join(LOGS_FOLDER, "general.log")
builder_log_path = os.path.join(LOGS_FOLDER, "builder.log")
system_log_path = os.path.join(LOGS_FOLDER, "system.log")

general_handler = setup_file_handler(general_log_path)
logger.addHandler(general_handler)

builder_handler = setup_file_handler(builder_log_path, category_filter=BuilderFilter())
logger.addHandler(builder_handler)

system_handler = setup_file_handler(system_log_path, category_filter=SystemFilter())
logger.addHandler(system_handler)

ITEM_WIDTH = 200
ITEM_HEIGHT = 100

build_in_progress = False  # Флаг, отслеживающий идет ли сборка
total_plugins_count = 0  # Общее количество плагинов для сборки
completed_plugins_count = 0  # Сколько плагинов уже собрано
current_columns = 3
plugins_data = []
data_builder = {'plugin_data': []}  # Инициализируем с ключом 'plugin_data'
current_row = 0
current_column = 0
engine_builder_path = None
gif_frames = None
empty_block_count = 0  # количество пустых блоков
pending_plugins_count = 0  # Счетчик ожидаемых плагинов

# Определение путей для каждой ОС через словарь
os_search_dirs = {
    'nt': [r"C:\Program Files", r"C:\Program Files (x86)", r"D:\\", r"E:\\"],
    'posix': [os.path.expanduser("~"), "/Applications", "/usr/local"]
}

# Буферная папка
BUFFER_FOLDER = "buffer"
if not os.path.exists(BUFFER_FOLDER):
    os.makedirs(BUFFER_FOLDER)

# Инициализация основного окна
root = tk.Tk()
root.title(translate("title"))
root.geometry("900x600")
root.configure(bg="#f0f0f0")

def log_message(message_key, category="user", msg_type="info", *args):
    """
    Функция для вывода отладочной информации в лог и GUI.

    :param message_key: Ключ сообщения для логирования.
    :param category: Категория сообщения ('user', 'builder', 'system', и т.д.).
    :param msg_type: Тип сообщения ('info', 'warning', 'error', 'builder_info', successes).
    :param args: Дополнительные аргументы для форматирования строки.
    """
    # Получаем переведённое сообщение
    message_template = translate(message_key)
    message = message_template.format(*args) if args else message_template

    # Определяем уровень логирования на основе msg_type
    if msg_type == "info":
        level = logging.DEBUG
    elif msg_type == "warning":
        level = logging.WARNING
    elif msg_type == "successes":
        level = logging.INFO
    elif msg_type == "error":
        level = logging.ERROR
    else:
        level = logging.DEBUG

    # Добавляем сообщение в логгер с дополнительными полями
    logger.log(level, message, extra={'category': category})

    # Упрощенное сообщение для GUI
    simplified_message = message  # Здесь можно добавить логику для упрощения сообщений

    # Определяем тег на основе категории и типа сообщения
    if msg_type == "builder_info":
        gui_tag = "builder_info"
    elif msg_type == "info":
        gui_tag = "info"
    elif msg_type == "successes":
        gui_tag = "successes"
    elif msg_type == "warning":
        gui_tag = "warning"
    elif msg_type == "error":
        gui_tag = "error"
    else:
        gui_tag = "info"

    # Отображаем сообщение в GUI для категорий 'user'
    if category in ["user"]:
        log_text.insert(tk.END, f"{simplified_message}\n", gui_tag)
        log_text.see(tk.END)
        root.update_idletasks()

try:
    # Путь к иконкам
    icon_path_windows = os.path.join("public", "icon.ico")
    icon_path_png = os.path.join("public", "icon.png")
    
    if os.path.exists(icon_path_windows):
        root.iconbitmap(icon_path_windows)
    elif os.path.exists(icon_path_png):
        icon = ImageTk.PhotoImage(Image.open(icon_path_png))
        root.iconphoto(True, icon)
    else:
        log_message("icon_not_found", "system", "warning")
except Exception as e:
    log_message("error_setting_icon", "system", "error", e)

def set_bg_recursive(widget, color):
    try:
        widget.configure(bg=color)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        set_bg_recursive(child, color)

def update_plugin_color(plugin, color):
    if 'frame' in plugin and plugin['frame']:
        def change_color():
            set_bg_recursive(plugin['frame'], color)
        root.after(0, change_color)
    else:
        log_message(
            "no_frame_reference_plugin",
            "system",
            "warning",
            plugin.get('FriendlyName', translate("unknown_plugin"))
        )

def delete_rebuilt_plugins_folder():
    """
    Удаляет папку RebuiltPlugins при запуске приложения, если она существует.
    """
    if REBUILT_PLUGINS_FOLDER.exists() and REBUILT_PLUGINS_FOLDER.is_dir():
        try:
            shutil.rmtree(REBUILT_PLUGINS_FOLDER)
            log_message("deleted_rebuilt_plugins_folder", "system", "successes", str(REBUILT_PLUGINS_FOLDER))
        except Exception as e:
            log_message("error_deleting_rebuilt_plugins_folder", "system", "error", e)
    else:
        log_message("rebuilt_plugins_folder_not_exists", "system", "info", str(REBUILT_PLUGINS_FOLDER))

def find_epic_games():
    # Ищем директории, в которых может быть установлен Epic Games
    for base_dir in os_search_dirs.get(os.name, []):
        epic_games_path = os.path.join(base_dir, "Epic Games")
        if os.path.isdir(epic_games_path):
            if any(item.startswith("UE_") and os.path.isdir(os.path.join(epic_games_path, item)) for item in os.listdir(epic_games_path)):
                return epic_games_path
        else:
            log_message("directory_not_exists", "user", "warning", epic_games_path)
    log_message("epic_games_not_found", "user", "warning")
    return None

def find_engine_versions():
    versions = []
    base_path = find_epic_games()
    if base_path and os.path.exists(base_path):
        for folder in os.listdir(base_path):
            if folder.startswith("UE_"):
                version = folder[3:]  # Извлекаем версию после "UE_"
                versions.append(version)
    else:
        log_message("failed_to_find_epic_games_path", "user", "warning")
    return versions

def select_plugins():
    plugin_folders = filedialog.askdirectory(title=translate("select_plugin_folder_title"))
    if plugin_folders:
        log_message("selected_plugin_folder", "user", "info", plugin_folders)
        # Копируем выбранную папку целиком в буфер
        target_path = os.path.join(BUFFER_FOLDER, os.path.basename(plugin_folders))
        if os.path.exists(target_path):
            shutil.rmtree(target_path)
            log_message("deleted_old_copy", "system", "info", target_path)
        shutil.copytree(plugin_folders, target_path)
        log_message("copied_to_buffer", "system", "info", target_path)
        # В данном случае всего один плагин
        global pending_plugins_count
        pending_plugins_count = 1
        create_empty_plugin_blocks(1)
        load_plugin_data(target_path)

def preview_plugins_from_zip():
    zip_files = filedialog.askopenfilenames(
        title=translate("select_zip_title"),
        filetypes=[(translate("zip_files"), "*.zip")]
    )
    if zip_files:
        log_message("selected_zip_files", "user", "info", ', '.join(zip_files))
        # Подсчёт общего числа плагин-папок
        total_folders = 0
        for zip_file in zip_files:
            with zipfile.ZipFile(zip_file, 'r') as zf:
                folders = {os.path.dirname(name).split("/")[0] for name in zf.namelist() if name.endswith("/")}
                total_folders += len(folders)
        log_message("total_plugins_in_zip", "system", "info", total_folders)

        # Устанавливаем счетчик ожидаемых плагинов
        global pending_plugins_count
        pending_plugins_count = total_folders

        create_empty_plugin_blocks(total_folders)
        import_plugins_from_zip_async(zip_files)

def create_empty_plugin_blocks(count):
    global current_row, current_column, empty_block_count
    log_message("creating_loading_blocks", "system", "info", count)
    empty_block_count = count
    current_row, current_column = 0, 0

    for widget in plugins_inner_frame.winfo_children():
        widget.destroy()

    for _ in range(count):
        plugin_frame = tk.Frame(plugins_inner_frame, bg="#f0f0f0", bd=1, relief="ridge",
                                highlightthickness=1, highlightbackground="#999999",
                                width=ITEM_WIDTH, height=ITEM_HEIGHT)
        plugin_frame.grid(row=current_row, column=current_column, padx=10, pady=10, sticky="nsew")
        plugin_frame.pack_propagate(False)

        if gif_frames:
            loading_label = tk.Label(plugin_frame, bg="#f0f0f0")
            loading_label.pack(expand=True)
            animate_loading(loading_label, gif_frames)
        else:
            tk.Label(plugin_frame, text=translate("loading"), bg="#f0f0f0",
                     font=("Arial", 10, "italic"), fg="#777777").pack(expand=True)

        current_column += 1
        if current_column >= current_columns:
            current_column = 0
            current_row += 1

    root.after(0, update_scrollregion)

def preload_gif_frames(gif=None):
    gif_path = os.path.join("public", "loading.gif")
    frames = []
    try:
        gif = Image.open(gif_path)
        while True:
            frame = ImageTk.PhotoImage(gif.copy())
            frames.append(frame)
            gif.seek(len(frames))
    except EOFError:
        gif.seek(0)
    except Exception as e:
        log_message("error_loading_gif", "system", "error", e)
    return frames

def animate_loading(label, frames, frame_index=0):
    try:
        label.config(image=frames[frame_index])
        label.image = frames[frame_index]
        next_frame = (frame_index + 1) % len(frames)
        label.after(45, animate_loading, label, frames, next_frame)
    except Exception as e:
        log_message("error_animating_gif", "system", "warning", e)

def import_plugins_from_zip_async(zip_files):
    log_message("import_plugins_from_zip_start", "system", "info")
    threading.Thread(target=lambda: import_plugins_from_zip(zip_files), daemon=True).start()

def import_plugins_from_zip(zip_files):
    for zip_file in zip_files:
        log_message("extracting_zip_file", "system", "info", zip_file)
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            log_message("zip_extracted", "system", "info", temp_dir)

            # Копируем распакованные плагины в буфер
            for item in os.listdir(temp_dir):
                source_path = os.path.join(temp_dir, item)
                target_path = os.path.join(BUFFER_FOLDER, item)
                if os.path.exists(target_path):
                    shutil.rmtree(target_path)
                    log_message("deleted_old_copy", "system", "info", target_path)
                shutil.copytree(source_path, target_path)
                log_message("copied_to_buffer", "system", "info", target_path)

            # Загружаем плагины из буфера
            for item in os.listdir(temp_dir):
                target_path = os.path.join(BUFFER_FOLDER, item)
                log_message("loading_plugin_data", "system", "info", target_path)
                # Загрузка плагина отложена в основной поток
                root.after(0, load_plugin_data, target_path)

def load_plugin_data(plugin_folder):
    global empty_block_count, pending_plugins_count
    log_message("reading_plugin_data", "system", "info", plugin_folder)
    found_plugin = False

    for root_folder, _, files in os.walk(plugin_folder):
        for file in files:
            if file.endswith(".uplugin"):
                plugin_path = os.path.join(root_folder, file)
                try:
                    with open(plugin_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        data['PluginPath'] = plugin_path
                        data['IconPath'] = os.path.join(root_folder, "Resources", "Icon128.png")

                        # Добавляем данные в plugins_data
                        plugin_entry = {
                            "FriendlyName": data.get("FriendlyName", translate("unknown_plugin")),
                            "VersionName": data.get("VersionName", translate("n_a")),
                            "EngineVersion": data.get("EngineVersion", translate("n_a")),
                            "IconPath": data['IconPath'],
                            "PluginPath": plugin_path,
                            "frame": None,  # Будет заполнено при отображении
                            "widgets": {}    # Будет заполнено при отображении
                        }
                        plugins_data.append(plugin_entry)

                        # Добавляем данные в data_builder['plugin_data']
                        data_builder['plugin_data'].append({
                            "name": data.get("FriendlyName", translate("unknown_plugin")),
                            "path": plugin_path,
                            "plugin_ref": plugin_entry  # Ссылка на запись плагина для обновления GUI
                        })

                        found_plugin = True
                        log_message("found_plugin", "system", "info", data.get("FriendlyName", translate("unknown_plugin")))
                except json.JSONDecodeError:
                    log_message("error_loading_plugin_json", "system", "warning", file)

    if not found_plugin:
        log_message("no_plugins_found", "user", "warning")

    # Уменьшаем счетчик ожидаемых плагинов
    pending_plugins_count -= 1

    # Если все плагины загружены, обновляем список
    if pending_plugins_count == 0:
        # Убираем пустые блоки
        empty_block_count = 0
        log_message("all_plugins_loaded", "system", "info")
        display_plugins()

def create_plugin_block(plugin):
    global current_row, current_column, current_columns
    plugin_frame = tk.Frame(plugins_inner_frame, bg="#ffffff", bd=1, relief="ridge",
                            highlightthickness=1, highlightbackground="#777777",
                            width=ITEM_WIDTH, height=ITEM_HEIGHT)
    plugin_frame.grid(row=current_row, column=current_column, padx=10, pady=10, sticky="nsew")
    plugin_frame.pack_propagate(False)

    if os.path.exists(plugin['IconPath']):
        try:
            icon = Image.open(plugin['IconPath']).resize((48, 48))
            icon = ImageTk.PhotoImage(icon)
        except Exception as e:
            log_message("error_loading_icon", "system", "warning", plugin['IconPath'], e)
            icon = ImageTk.PhotoImage(Image.new("RGB", (48, 48), color="#CCCCCC"))
    else:
        icon = ImageTk.PhotoImage(Image.new("RGB", (48, 48), color="#CCCCCC"))

    # Сохранение ссылок на виджеты для дальнейшего изменения их свойств
    plugin['widgets'] = {}

    # Имя плагина
    name_label = tk.Label(plugin_frame, text=plugin.get("FriendlyName", translate("unknown_plugin")),
                          font=("Arial", 10, "bold"), bg="#ffffff", fg="#333333")
    name_label.pack(side="top", fill="x", pady=(0, 2))
    plugin['widgets']['name_label'] = name_label

    # Нижняя часть с иконкой и информацией
    bottom_frame = tk.Frame(plugin_frame, bg="#ffffff")
    bottom_frame.pack(side="bottom", fill="both", expand=True, padx=0, pady=0)

    bottom_inner_frame = tk.Frame(bottom_frame, bg="#ffffff")
    bottom_inner_frame.pack(anchor="center", expand=True)

    icon_label = tk.Label(bottom_inner_frame, image=icon, bg="#ffffff")
    icon_label.image = icon
    icon_label.pack(side="left", padx=5, pady=5)
    plugin['widgets']['icon_label'] = icon_label

    info_text = f"{translate('version')}: {plugin.get('VersionName', translate('n_a'))}\n{translate('engine')}: {plugin.get('EngineVersion', translate('n_a'))}"
    info_label = tk.Label(bottom_inner_frame, text=info_text, font=("Arial", 9), bg="#ffffff", fg="#555555")
    info_label.pack(side="left", padx=5, pady=5)
    plugin['widgets']['info_label'] = info_label

    # Кнопка удаления
    delete_button = tk.Button(plugin_frame, text=translate("delete"), command=lambda p=plugin: delete_plugin(p),
                              bg="#f44336", fg="white", font=("Arial", 7, "bold"), relief="flat")
    delete_button.place(relx=1.0, rely=0.0, anchor="ne", y=0)
    delete_button.lower()  # Отправляем кнопку на задний план, чтобы она была скрыта

    plugin['widgets']['delete_button'] = delete_button

    # Функции для показа и скрытия кнопки удаления при наведении мыши
    def on_enter(event):
        delete_button.lift()  # Поднимаем кнопку на передний план
        delete_button.place(relx=1.0, rely=0.0, anchor="ne", y=0)  # Убедимся, что кнопка на месте с отступом

    def on_leave(event):
        delete_button.lower()  # Отправляем кнопку обратно на задний план

    # Привязываем события наведения и ухода мыши к фрейму плагина
    plugin_frame.bind("<Enter>", on_enter)
    plugin_frame.bind("<Leave>", on_leave)

    # Сохраняем ссылку на Frame в данных плагина
    plugin['frame'] = plugin_frame

    current_column += 1
    if current_column >= current_columns:
        current_column = 0
        current_row += 1

def delete_plugin(plugin):
    # Удаляем фрейм из GUI
    if plugin.get('frame'):
        plugin['frame'].destroy()
        log_message("plugin_deleted", "user", "info", plugin.get('FriendlyName', translate("unknown_plugin")))
    else:
        log_message("no_frame_reference_plugin_delete", "system", "warning", plugin.get('FriendlyName', translate("unknown_plugin")))

    # Удаляем из plugins_data
    if plugin in plugins_data:
        plugins_data.remove(plugin)
        log_message("plugin_removed_from_data", "system", "info", plugin.get('FriendlyName', translate("unknown_plugin")))
    
    # Удаляем из data_builder['plugin_data']
    to_remove = None
    for item in data_builder['plugin_data']:
        if item.get('plugin_ref') == plugin:
            to_remove = item
            break
    if to_remove:
        data_builder['plugin_data'].remove(to_remove)
        log_message("plugin_removed_from_builder_data", "system", "info", plugin.get('FriendlyName', translate("unknown_plugin")))
    
    # Обновляем счетчики, если сборка еще не завершена
    global total_plugins_count, completed_plugins_count
    if build_in_progress:
        total_plugins_count -= 1
        log_message("total_plugins_decremented", "system", "info", total_plugins_count)
        if completed_plugins_count > total_plugins_count:
            completed_plugins_count = total_plugins_count
            log_message("completed_plugins_updated", "system", "info", completed_plugins_count)

    # Обновляем расположение оставшихся плагинов
    recreate_layout(current_columns)

def display_plugins():
    global current_row, current_column
    current_row, current_column = 0, 0

    for widget in plugins_inner_frame.winfo_children():
        widget.destroy()

    for plugin in plugins_data:
        create_plugin_block(plugin)

    root.after(0, update_scrollregion)

def update_scrollregion():
    plugins_canvas.update_idletasks()
    plugins_canvas.config(scrollregion=plugins_canvas.bbox("all"))

def on_frame_configure(event):
    # Синхронно обновляем макет при изменении размера
    update_scrollregion()
    frame_width = event.width
    col_width = ITEM_WIDTH + 20
    new_columns = max(1, frame_width // col_width)

    global current_columns
    if new_columns != current_columns:
        recreate_layout(new_columns)

def recreate_layout(new_columns):
    global current_columns
    current_columns = new_columns
    log_message("rebuilding_layout", "system", "info")
    # Перестраиваем макет только тогда, когда плагины уже загружены и отрисованы
    if pending_plugins_count == 0:
        if plugins_data:
            display_plugins()
        else:
            # Если плагинов нет, возможно пустые блоки
            if empty_block_count > 0:
                create_empty_plugin_blocks(empty_block_count)
    else:
        # Плагины ещё загружаются
        # Перестраиваем пустые блоки даже если плагины ещё не загружены
        if empty_block_count > 0:
            create_empty_plugin_blocks(empty_block_count)

def on_canvas_configure(event):
    canvas_width = event.width
    plugins_canvas.itemconfig(inner_frame_id, width=canvas_width)

def on_mousewheel(event):
    widget_under_mouse = root.winfo_containing(event.x_root, event.y_root)
    if widget_under_mouse is not None and is_descendant(widget_under_mouse, plugins_canvas):
        plugins_canvas.yview_scroll(int(-event.delta/120), "units")

def is_descendant(child, parent):
    w = child
    while w is not None:
        if w == parent:
            return True
        w = w.master
    return False

def cleanup_buffer():
    if os.path.exists(BUFFER_FOLDER):
        try:
            shutil.rmtree(BUFFER_FOLDER)
            log_message("buffer_cleared", "system", "info")
        except Exception as e:
            log_message("error_clearing_buffer", "system", "error", e)

def archive_all_logs():
    """
    Архивирует все лог-файлы из папки LOGS_FOLDER в один ZIP-архив с текущей датой
    и сохраняет его в папку ARCHIVES_FOLDER.
    """
    try:
        # Форматируем текущую дату
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        archive_name = os.path.join(ARCHIVES_FOLDER, f"logs_{current_date}.zip")
        
        # Проверяем, существует ли уже архив с текущей датой
        if os.path.exists(archive_name):
            log_message("archive_already_exists", "system", "info", archive_name)
            return
        
        log_files = glob.glob(os.path.join(LOGS_FOLDER, "*.log"))
        if not log_files:
            log_message("no_logs_to_archive", "system", "info")
            return
        
        with zipfile.ZipFile(archive_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for log_file in log_files:
                zipf.write(log_file, arcname=os.path.basename(log_file))
        
        # Очистка лог-файлов после архивации (опционально)
        for log_file in log_files:
            with open(log_file, 'w', encoding='utf-8'):
                pass  # Открываем файл для записи, что приведёт к его очистке
        
        log_message("logs_archived", "system", "successes", archive_name)
    except Exception as e:
        log_message("error_archiving_logs", "system", "error", e)

def log_separator():
    """
    Записывает разделительное сообщение в логи для всех категорий.
    """
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator_message = translate("log_separator").format(current_time)
    
    # Записываем в категории 'builder' и 'system'
    for category in ["builder", "system"]:
        log_message("log_separator_message", category, "info", current_time)

def on_closing():
    """
    Функция, вызываемая при закрытии приложения.
    Выполняет архивирование логов при необходимости и очищает буфер.
    """
    try:
        # Проверяем, нужно ли архивировать логи
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        archive_name = os.path.join(ARCHIVES_FOLDER, f"logs_{current_date}.zip")
        if not os.path.exists(archive_name):
            archive_all_logs()
        else:
            log_message("archive_already_exists_on_close", "system", "info", archive_name)
        
        cleanup_buffer()
        log_message("app_closing", "user", "info")
        
        # Завершение всех таймеров и фоновых потоков
        for thread in threading.enumerate():
            if thread is not threading.main_thread():
                log_message("thread_terminating", "system", "info", thread.name)
                thread.join(timeout=1)
    except Exception as e:
        log_message("error_on_closing", "system", "error", e)
    finally:
        root.destroy()

def on_selected_engine(event):
    selected_version = engine_dropdown.get()
    epic_games_path = find_epic_games()
    if epic_games_path:
        data_builder['builder_path'] = os.path.join(epic_games_path, f"UE_{selected_version}", "Engine", "Build", "BatchFiles")
        log_message("builder_path_set", "user", "info", data_builder['builder_path'])
    else:
        log_message("builder_path_not_set_epic_not_found", "user", "warning")

def start_build_thread():
    global build_in_progress, total_plugins_count, completed_plugins_count
    
    if 'builder_path' not in data_builder:
        log_message("builder_path_not_set", "user", "warning")
        return
    if not data_builder.get('plugin_data'):
        log_message("no_plugins_to_build", "user", "warning")
        return

    # Устанавливаем флаг в True, чтобы указать, что сборка началась
    build_in_progress = True
    completed_plugins_count = 0  # Сбрасываем счетчик завершенных сборок
    total_plugins_count = len(data_builder['plugin_data'])  # Считаем общее количество плагинов

    # Блокируем кнопку сборки, чтобы избежать повторных запусков
    rebuild_button.config(state=tk.DISABLED)
    
    build_thread = threading.Thread(target=build_plugins, args=(data_builder, log_message), daemon=True)
    build_thread.start()

def build_plugins(data, callback=None):
    global completed_plugins_count, build_in_progress, total_plugins_count
    
    if data:
        plugin_data = data.get('plugin_data', [])
        builder_path = data.get('builder_path')
        output_base = REBUILT_PLUGINS_FOLDER  # Используем глобальную переменную

        run_uat = Path(builder_path) / "RunUAT.bat"
        if not run_uat.exists():
            if callback:
                callback("error_run_uat_not_found", "user", "error", str(run_uat))
            return

        output_base.mkdir(parents=True, exist_ok=True)

        for plugin in plugin_data:
            name = plugin.get('name')
            path = plugin.get('path')
            plugin_ref = plugin.get('plugin_ref')  # Получаем ссылку на запись плагина

            if not name or not path:
                if callback:
                    callback("plugin_skipped_missing_info", "user", "warning", name)
                continue

            plugin_path = Path(path).resolve()
            if not plugin_path.exists():
                if callback:
                    callback("plugin_path_not_exists", "user", "warning", str(plugin_path))
                continue

            package_folder = output_base / name
            package_folder.mkdir(parents=True, exist_ok=True)

            command = f'"{run_uat}" BuildPlugin -plugin="{plugin_path}" -package="{package_folder}"'

            if callback:
                callback("building_plugin", "user", "builder_info", name)

            if callback:
                callback("executing_command", "builder", "info", command)

            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                for line in process.stdout:
                    line = line.strip()
                    if re.match(r"^\[\d+/\d+\] (.*)$", line):
                        if callback:
                            callback("builder_output", "user", "builder_info", line)
                    elif "Building" in line or "Compile" in line or "Link" in line:
                        if callback:
                            callback("builder_output", "user", "builder_info", line)

                for line in process.stderr:
                    if callback:
                        callback("builder_error_output", "builder", "error", line.strip())

                process.wait()

                if process.returncode == 0:
                    if callback:
                        callback("plugin_build_success", "user", "successes", name)
                        callback("plugin_build_success_builder", "builder", "info", name)
                    
                    # Изменяем цвет блока плагина на зелёный
                    update_plugin_color(plugin_ref, "green")
                else:
                    if callback:
                        callback("plugin_build_error", "builder", "error", name)
                    
                    # Изменяем цвет блока плагина на красный
                    update_plugin_color(plugin_ref, "red")

            except subprocess.CalledProcessError as e:
                if callback:
                    callback("plugin_build_exception", "builder", "error", name, e)
                
                # Изменяем цвет блока плагина на красный
                update_plugin_color(plugin_ref, "red")

            # После сборки каждого плагина увеличиваем счетчик завершенных плагинов
            completed_plugins_count += 1
            log_message("plugins_built_progress", "user", "info", completed_plugins_count, total_plugins_count)

            # Проверяем, завершена ли сборка всех плагинов
            if completed_plugins_count == total_plugins_count:
                # Разблокируем кнопку, когда все плагины собраны
                build_in_progress = False
                rebuild_button.config(state=tk.NORMAL)
                log_message("all_plugins_built", "system", "successes")
                
                # Отображаем кнопку для открытия папки
                root.after(0, open_folder_button.pack)

# Создаем интерфейс
main_frame = tk.Frame(root, bg="#f0f0f0")
main_frame.pack(fill="both", expand=True)

left_frame_top = tk.Frame(main_frame, bg="#ffffff", relief="ridge", bd=2)
left_frame_top.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

left_frame_bottom = tk.Frame(main_frame, bg="#ffffff", relief="ridge", bd=2)
left_frame_bottom.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

plugins_frame = tk.Frame(main_frame, bg="#ffffff", relief="groove", bd=2)
plugins_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

main_frame.rowconfigure(0, weight=3)
main_frame.rowconfigure(1, weight=1)
main_frame.columnconfigure(0, weight=1)
main_frame.columnconfigure(1, weight=3)

plugins_canvas = tk.Canvas(plugins_frame, bg="#ffffff")
plugins_canvas.pack(side="left", fill="both", expand=True)

plugins_inner_frame = tk.Frame(plugins_canvas, bg="#ffffff")
inner_frame_id = plugins_canvas.create_window((0, 0), window=plugins_inner_frame, anchor="nw")

log_frame = tk.Frame(main_frame, bg="#ffffff", relief="ridge", bd=2)
log_frame.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)

# Определение и настройка тегов для Text виджета
log_text = tk.Text(log_frame, height=8, wrap="word", bg="#f0f0f0", font=("Arial", 9))
log_text.pack(fill="both", expand=True, padx=5, pady=5)

# Определяем теги для различных типов сообщений
log_text.tag_configure("info", foreground="grey")       # Сообщения от пользователя
log_text.tag_configure("builder_info", foreground="blue")     # Сообщения от билдера
log_text.tag_configure("system_special", foreground="red")    # Специальные сообщения (ошибки)
log_text.tag_configure("successes", foreground="green")            # Общие информационные сообщения
log_text.tag_configure("warning", foreground="orange")        # Предупреждения
log_text.tag_configure("error", foreground="red", font=("Arial", 9, "bold"))  # Ошибки

# Добавляем кнопки и другие элементы интерфейса
select_folder_label = tk.Label(left_frame_top, text=translate("select_folder"), bg="#ffffff", anchor="w", font=("Arial", 10))
select_folder_label.pack(pady=5)

select_folder_button = tk.Button(left_frame_top, text=translate("select_plugin_folder"), command=select_plugins, bg="#4caf50", fg="white", font=("Arial", 10, "bold"), relief="flat")
select_folder_button.pack(pady=5)

select_zip_button = tk.Button(left_frame_top, text=translate("select_zip"), command=preview_plugins_from_zip, bg="#2196F3", fg="white", font=("Arial", 10, "bold"), relief="flat")
select_zip_button.pack(pady=5)

select_version_label = tk.Label(left_frame_bottom, text=translate("select_engine_version"), bg="#ffffff", anchor="w", font=("Arial", 10))
select_version_label.pack(pady=5)

engine_var = tk.StringVar()
engine_dropdown = ttk.Combobox(left_frame_bottom, textvariable=engine_var, values=find_engine_versions(), state="readonly", width=20)
engine_dropdown.pack(pady=5)

rebuild_button = tk.Button(left_frame_bottom, text=translate("rebuild_plugins"), command=start_build_thread, bg="#4caf50", fg="white", font=("Arial", 10, "bold"), relief="flat")
rebuild_button.pack(pady=5)

# Добавляем кнопку для открытия папки сборки (скрытую изначально)
open_folder_button = tk.Button(
    left_frame_bottom,
    text=translate("open_build_folder"),
    command=lambda: open_rebuilt_plugins_folder(),
    bg="#FF9800",
    fg="white",
    font=("Arial", 10, "bold"),
    relief="flat"
)
open_folder_button.pack(pady=5)
# Скрываем кнопку изначально
open_folder_button.pack_forget()

# Определение функции для открытия папки
def open_rebuilt_plugins_folder():
    try:
        path = REBUILT_PLUGINS_FOLDER.resolve()
        if not path.exists():
            log_message("build_folder_not_exists", "user", "warning", str(path))
            return
        if os.name == 'nt':  # Windows
            os.startfile(str(path))
        elif os.name == 'posix':  # macOS, Linux
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        else:
            log_message("unsupported_os_for_open_folder", "user", "error")
    except Exception as e:
        log_message("error_opening_folder", "user", "error", e)

# Добавление выпадающего списка для выбора языка
def change_language(event=None):
    global current_language
    selected_lang = language_var.get()
    if selected_lang != current_language:
        current_language = selected_lang
        load_language(current_language)
        update_ui_language()

language_var = tk.StringVar(value=current_language)  # Установите язык по умолчанию
language_dropdown = ttk.Combobox(left_frame_bottom, textvariable=language_var, values=["ru", "en"], state="readonly", width=10)
language_dropdown.pack(pady=5)
language_dropdown.bind("<<ComboboxSelected>>", change_language)

# Функция обновления интерфейса при смене языка
def update_ui_language():
    root.title(translate("title"))
    
    # Обновляем метки и кнопки в left_frame_top
    select_folder_label.config(text=translate("select_folder"))
    select_folder_button.config(text=translate("select_plugin_folder"))
    select_zip_button.config(text=translate("select_zip"))
    
    # Обновляем метки и кнопки в left_frame_bottom
    select_version_label.config(text=translate("select_engine_version"))
    rebuild_button.config(text=translate("rebuild_plugins"))
    open_folder_button.config(text=translate("open_build_folder"))
    
    # Обновляем другие элементы интерфейса по необходимости
    # Например, кнопки удаления в плагинах уже обновляются через логическую систему

# Обработка закрытия окна
root.protocol("WM_DELETE_WINDOW", on_closing)
plugins_inner_frame.bind("<Configure>", on_frame_configure)
plugins_canvas.bind('<Configure>', on_canvas_configure)
root.bind_all("<MouseWheel>", on_mousewheel)
engine_dropdown.bind("<<ComboboxSelected>>", on_selected_engine)
# Вызов функции разделителя после настройки логирования
log_separator()

# Удаление папки RebuiltPlugins при запуске
delete_rebuilt_plugins_folder()

# Запуск приложения и вывод стартового сообщения
log_message("app_started", "user", "info")
root.mainloop()
