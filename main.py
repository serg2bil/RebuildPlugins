import sys
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

def resource_path(relative_path):
    """Gets the absolute path to the resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Global variable for translations
translations = {}

def load_language(lang_code):
    """
    Loads the translation file for the specified language.

    :param lang_code: Language code (e.g., 'ru', 'en').
    """
    global translations
    try:
        locale_path = resource_path(os.path.join("locales", f"{lang_code}.json"))
        with open(locale_path, 'r', encoding='utf-8') as f:
            translations = json.load(f)
    except FileNotFoundError:
        messagebox.showerror("Error", f"Translation file for language '{lang_code}' not found.")
    except json.JSONDecodeError:
        messagebox.showerror("Error", f"Translation file for language '{lang_code}' contains invalid JSON.")

def translate(key):
    """
    Returns the translated string for the given key.

    :param key: The key string in the translation file.
    :return: Translated string or the key itself if translation is not found.
    """
    return translations.get(key, key)

# Load default language
current_language = "en"
load_language(current_language)

# Create "logs" and "logs/archives" folders if they don't exist
LOGS_FOLDER = "logs"
ARCHIVES_FOLDER = os.path.join(LOGS_FOLDER, "archives")

for folder in [LOGS_FOLDER, ARCHIVES_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Define path for RebuiltPlugins folder
REBUILT_PLUGINS_FOLDER = Path(__file__).parent / "RebuiltPlugins"

# Logging setup
logger = logging.getLogger("PluginBuilder")
logger.setLevel(logging.DEBUG)  # Capture all logging levels

# Create a formatter that includes the category
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(category)s - %(message)s')

# Filters for 'builder' and 'system' categories
class BuilderFilter(logging.Filter):
    def filter(self, record):
        return getattr(record, 'category', '') == "builder"

class SystemFilter(logging.Filter):
    def filter(self, record):
        return getattr(record, 'category', '') == "system"

def setup_file_handler(log_path, level=logging.DEBUG, category_filter=None):
    """
    Sets up a FileHandler without rotation.

    :param log_path: Path to the log file.
    :param level: Logging level.
    :param category_filter: Filter for the log category.
    :return: Configured handler.
    """
    handler = logging.FileHandler(log_path, encoding='utf-8')
    handler.setLevel(level)
    handler.setFormatter(formatter)
    
    if category_filter:
        handler.addFilter(category_filter)
    
    return handler

# Create log handlers without rotation
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

build_in_progress = False  # Flag to track if building is in progress
total_plugins_count = 0  # Total number of plugins to build
completed_plugins_count = 0  # Number of plugins already built
current_columns = 3
plugins_data = []
data_builder = {'plugin_data': []}  # Initialized with 'plugin_data' key
current_row = 0
current_column = 0
engine_builder_path = None
gif_frames = None
empty_block_count = 0  # Number of empty blocks
pending_plugins_count = 0  # Counter for pending plugins

# Define search directories based on OS
os_search_dirs = {
    'nt': [r"C:\Program Files", r"C:\Program Files (x86)", r"D:\\", r"E:\\"],
    'posix': [os.path.expanduser("~"), "/Applications", "/usr/local"]
}

# Buffer folder
BUFFER_FOLDER = "buffer"
if not os.path.exists(BUFFER_FOLDER):
    os.makedirs(BUFFER_FOLDER)

# Initialize the main window
root = tk.Tk()
root.title(translate("title"))
root.geometry("900x600")
root.configure(bg="#f0f0f0")

def log_message(message_key, category="user", msg_type="info", *args):
    """
    Logs debug information to the log files and GUI.

    :param message_key: The key of the message to log.
    :param category: The category of the message ('user', 'builder', 'system', etc.).
    :param msg_type: The type of the message ('info', 'warning', 'error', 'builder_info', 'successes').
    :param args: Additional arguments for formatting the message.
    """
    # Get the translated message
    message_template = translate(message_key)
    message = message_template.format(*args) if args else message_template

    # Determine logging level based on msg_type
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

    # Log the message with the category
    logger.log(level, message, extra={'category': category})

    # Simplified message for GUI
    simplified_message = message  # Add logic here if needed for simplification

    # Determine tag based on message type
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

    # Display message in GUI for 'user' category
    if category in ["user"]:
        log_text.insert(tk.END, f"{simplified_message}\n", gui_tag)
        log_text.see(tk.END)
        root.update_idletasks()

try:
    # Path to the icon
    icon_path = resource_path(os.path.join("public", "icon.png"))
    icon = ImageTk.PhotoImage(Image.open(icon_path))
    root.iconphoto(True, icon)
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
    Deletes the RebuiltPlugins folder on application startup if it exists.
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
    # Search directories where Epic Games might be installed
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
                version = folder[3:]  # Extract version after "UE_"
                versions.append(version)
    else:
        log_message("failed_to_find_epic_games_path", "user", "warning")
    return versions

def select_plugins():
    plugin_folders = filedialog.askdirectory(title=translate("select_plugin_folder_title"))
    if plugin_folders:
        log_message("selected_plugin_folder", "user", "info", plugin_folders)
        # Copy the selected folder entirely to the buffer
        target_path = os.path.join(BUFFER_FOLDER, os.path.basename(plugin_folders))
        if os.path.exists(target_path):
            shutil.rmtree(target_path)
            log_message("deleted_old_copy", "system", "info", target_path)
        shutil.copytree(plugin_folders, target_path)
        log_message("copied_to_buffer", "system", "info", target_path)
        # In this case, only one plugin
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
        # Count the total number of plugin folders
        total_folders = 0
        for zip_file in zip_files:
            with zipfile.ZipFile(zip_file, 'r') as zf:
                folders = {os.path.dirname(name).split("/")[0] for name in zf.namelist() if name.endswith("/")}
                total_folders += len(folders)
        log_message("total_plugins_in_zip", "system", "info", total_folders)

        # Set the counter for expected plugins
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
    gif_path = resource_path(os.path.join("public", "loading.gif"))
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

            # Copy extracted plugins to the buffer
            for item in os.listdir(temp_dir):
                source_path = os.path.join(temp_dir, item)
                target_path = os.path.join(BUFFER_FOLDER, item)
                if os.path.exists(target_path):
                    shutil.rmtree(target_path)
                    log_message("deleted_old_copy", "system", "info", target_path)
                shutil.copytree(source_path, target_path)
                log_message("copied_to_buffer", "system", "info", target_path)

            # Load plugins from the buffer
            for item in os.listdir(temp_dir):
                target_path = os.path.join(BUFFER_FOLDER, item)
                log_message("loading_plugin_data", "system", "info", target_path)
                # Loading plugin is deferred to the main thread
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

                        # Add data to plugins_data
                        plugin_entry = {
                            "FriendlyName": data.get("FriendlyName", translate("unknown_plugin")),
                            "VersionName": data.get("VersionName", translate("n_a")),
                            "EngineVersion": data.get("EngineVersion", translate("n_a")),
                            "IconPath": data['IconPath'],
                            "PluginPath": plugin_path,
                            "frame": None,  # Will be filled when displayed
                            "widgets": {}    # Will be filled when displayed
                        }
                        plugins_data.append(plugin_entry)

                        # Add data to data_builder['plugin_data']
                        data_builder['plugin_data'].append({
                            "name": data.get("FriendlyName", translate("unknown_plugin")),
                            "path": plugin_path,
                            "plugin_ref": plugin_entry  # Reference to the plugin entry for GUI updates
                        })

                        found_plugin = True
                        log_message("found_plugin", "system", "info", data.get("FriendlyName", translate("unknown_plugin")))
                except json.JSONDecodeError:
                    log_message("error_loading_plugin_json", "system", "warning", file)

    if not found_plugin:
        log_message("no_plugins_found", "user", "warning")

    # Decrease the pending plugins counter
    pending_plugins_count -= 1

    # If all plugins are loaded, update the list
    if pending_plugins_count == 0:
        # Remove empty blocks
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

    # Save widget references for later updates
    plugin['widgets'] = {}

    # Plugin name
    name_label = tk.Label(plugin_frame, text=plugin.get("FriendlyName", translate("unknown_plugin")),
                          font=("Arial", 10, "bold"), bg="#ffffff", fg="#333333")
    name_label.pack(side="top", fill="x", pady=(0, 2))
    plugin['widgets']['name_label'] = name_label

    # Bottom part with icon and info
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

    # Delete button
    delete_button = tk.Button(plugin_frame, text=translate("delete"), command=lambda p=plugin: delete_plugin(p),
                              bg="#f44336", fg="white", font=("Arial", 7, "bold"), relief="flat")
    delete_button.place(relx=1.0, rely=0.0, anchor="ne", y=0)
    delete_button.lower()  # Send the button to the back to hide it initially

    plugin['widgets']['delete_button'] = delete_button

    # Functions to show and hide the delete button on mouse hover
    def on_enter(event):
        delete_button.lift()  # Bring the button to the front
        delete_button.place(relx=1.0, rely=0.0, anchor="ne", y=0)  # Ensure the button stays in place with offset

    def on_leave(event):
        delete_button.lower()  # Send the button back to hide it

    # Bind mouse events to the plugin frame
    plugin_frame.bind("<Enter>", on_enter)
    plugin_frame.bind("<Leave>", on_leave)

    # Save reference to the frame in plugin data
    plugin['frame'] = plugin_frame

    current_column += 1
    if current_column >= current_columns:
        current_column = 0
        current_row += 1

def delete_plugin(plugin):
    """
    Deletes a plugin from the list and GUI.

    :param plugin: Dictionary containing plugin data.
    """
    confirm = messagebox.askyesno(translate("confirmation_delete").format(plugin.get('FriendlyName', translate("unknown_plugin"))))
    if confirm:
        # Remove the frame from the GUI
        if plugin.get('frame'):
            plugin['frame'].destroy()
            log_message("plugin_deleted", "user", "info", plugin.get('FriendlyName', translate("unknown_plugin")))
        else:
            log_message("no_frame_reference_plugin_delete", "system", "warning", plugin.get('FriendlyName', translate("unknown_plugin")))

        # Remove from plugins_data
        if plugin in plugins_data:
            plugins_data.remove(plugin)
            log_message("plugin_removed_from_data", "system", "info", plugin.get('FriendlyName', translate("unknown_plugin")))
        
        # Remove from data_builder['plugin_data']
        to_remove = None
        for item in data_builder['plugin_data']:
            if item.get('plugin_ref') == plugin:
                to_remove = item
                break
        if to_remove:
            data_builder['plugin_data'].remove(to_remove)
            log_message("plugin_removed_from_builder_data", "system", "info", plugin.get('FriendlyName', translate("unknown_plugin")))
        
        # Update counters if building is not completed
        global total_plugins_count, completed_plugins_count
        if build_in_progress:
            total_plugins_count -= 1
            log_message("total_plugins_decremented", "system", "info", total_plugins_count)
            if completed_plugins_count > total_plugins_count:
                completed_plugins_count = total_plugins_count
                log_message("completed_plugins_updated", "system", "info", completed_plugins_count)

        # Update the layout of remaining plugins
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
    # Synchronously update layout on size change
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
    # Rebuild layout only when plugins are loaded and displayed
    if pending_plugins_count == 0:
        if plugins_data:
            display_plugins()
        else:
            # If there are no plugins, possibly empty blocks
            if empty_block_count > 0:
                create_empty_plugin_blocks(empty_block_count)
    else:
        # Plugins are still loading
        # Rebuild empty blocks even if plugins are not yet loaded
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
    Archives all log files from LOGS_FOLDER into a single ZIP archive with the current date and time
    and saves it in ARCHIVES_FOLDER. Keeps archives for the last 14 days.
    """
    try:
       
        current_datetime = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        archive_name = os.path.join(ARCHIVES_FOLDER, f"logs_{current_datetime}.zip")
        
        log_message("archiving_logs_start", "system", "info", archive_name)
        
        log_files = glob.glob(os.path.join(LOGS_FOLDER, "*.log"))
        if not log_files:
            log_message("no_logs_to_archive", "system", "info")
            return
        
        
        with zipfile.ZipFile(archive_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for log_file in log_files:
                zipf.write(log_file, arcname=os.path.basename(log_file))
        
        log_message("logs_archived", "system", "successes", archive_name)
        
        
        for log_file in log_files:
            with open(log_file, 'w', encoding='utf-8'):
                pass  
        
        
        cleanup_old_archives()
        
    except Exception as e:
        log_message("error_archiving_logs", "system", "error", e)

def cleanup_old_archives(retention_days=14): # Number of days
    """
    Deletes log archives older than the specified number of days.

    :param retention_days: Number of days to retain archives.
    """
    try:
        now = datetime.datetime.now()
        cutoff_date = now - datetime.timedelta(days=retention_days)
        
        # Получение списка всех архивов в папке ARCHIVES_FOLDER
        archive_pattern = os.path.join(ARCHIVES_FOLDER, "logs_*.zip")
        archives = glob.glob(archive_pattern)
        
        for archive in archives:
            # Извлечение даты и времени из имени файла
            basename = os.path.basename(archive)
            try:
                date_str = basename.replace("logs_", "").replace(".zip", "")
                archive_datetime = datetime.datetime.strptime(date_str, "%Y-%m-%d_%H-%M-%S")
                
                if archive_datetime < cutoff_date:
                    os.remove(archive)
                    log_message("deleted_old_archive", "system", "info", archive)
            except ValueError:
                # Если формат имени файла не соответствует ожидаемому, пропускаем его
                log_message("invalid_archive_name_format", "system", "warning", basename)
                
    except Exception as e:
        log_message("error_cleanup_old_archives", "system", "error", e)


def log_separator():
    """
    Writes a separator message to the logs for all categories.
    """
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator_message = translate("log_separator").format(current_time)
    
    # Log for 'builder' and 'system' categories
    for category in ["builder", "system"]:
        log_message("log_separator_message", category, "info", current_time)

def on_closing():
    """
    Function called when the application is closing.
    Performs log archiving if necessary and cleans up the buffer.
    """
    try:
        # Check if logs need to be archived
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        archive_name = os.path.join(ARCHIVES_FOLDER, f"logs_{current_date}.zip")
        if not os.path.exists(archive_name):
            archive_all_logs()
        else:
            log_message("archive_already_exists_on_close", "system", "info", archive_name)
        
        cleanup_buffer()
        log_message("app_closing", "user", "info")
        
        # Terminate all timers and background threads
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

    # Set the flag to True to indicate that building has started
    build_in_progress = True
    completed_plugins_count = 0  # Reset the completed build counter
    total_plugins_count = len(data_builder['plugin_data'])  # Count total number of plugins

    # Disable the rebuild button to prevent multiple builds
    rebuild_button.config(state=tk.DISABLED)
    
    build_thread = threading.Thread(target=build_plugins, args=(data_builder, log_message), daemon=True)
    build_thread.start()

def build_plugins(data, callback=None):
    global completed_plugins_count, build_in_progress, total_plugins_count
    
    if data:
        plugin_data = data.get('plugin_data', [])
        builder_path = data.get('builder_path')
        output_base = REBUILT_PLUGINS_FOLDER  # Use the global variable

        run_uat = Path(builder_path) / "RunUAT.bat"
        if not run_uat.exists():
            if callback:
                callback("error_run_uat_not_found", "user", "error", str(run_uat))
            return

        output_base.mkdir(parents=True, exist_ok=True)

        for plugin in plugin_data:
            name = plugin.get('name')
            path = plugin.get('path')
            plugin_ref = plugin.get('plugin_ref')  # Get reference to the plugin entry

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
                    
                    # Change plugin block color to green
                    update_plugin_color(plugin_ref, "green")
                else:
                    if callback:
                        callback("plugin_build_error", "builder", "error", name)
                    
                    # Change plugin block color to red
                    update_plugin_color(plugin_ref, "red")

            except subprocess.CalledProcessError as e:
                if callback:
                    callback("plugin_build_exception", "builder", "error", name, e)
                
                # Change plugin block color to red
                update_plugin_color(plugin_ref, "red")

            # Increment the completed plugins counter after each build
            completed_plugins_count += 1
            log_message("plugins_built_progress", "user", "info", completed_plugins_count, total_plugins_count)

            # Check if all plugins have been built
            if completed_plugins_count == total_plugins_count:
                # Re-enable the rebuild button when all plugins are built
                build_in_progress = False
                rebuild_button.config(state=tk.NORMAL)
                log_message("all_plugins_built", "system", "successes")
                
                # Show the button to open the build folder
                root.after(0, open_folder_button.pack)

# Create the GUI
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

# Define and configure tags for the Text widget
log_text = tk.Text(log_frame, height=8, wrap="word", bg="#f0f0f0", font=("Arial", 9))
log_text.pack(fill="both", expand=True, padx=5, pady=5)

# Define tags for different message types
log_text.tag_configure("info", foreground="grey")       # User messages
log_text.tag_configure("builder_info", foreground="blue")     # Builder messages
log_text.tag_configure("system_special", foreground="red")    # Special messages (errors)
log_text.tag_configure("successes", foreground="green")            # General info messages
log_text.tag_configure("warning", foreground="orange")        # Warnings
log_text.tag_configure("error", foreground="red", font=("Arial", 9, "bold"))  # Errors

# Add buttons and other UI elements
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

# Add button to open the build folder (hidden initially)
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
# Hide the button initially
open_folder_button.pack_forget()

# Define function to open the build folder
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

# Add dropdown for language selection
def change_language(event=None):
    global current_language
    selected_lang = language_var.get()
    if selected_lang != current_language:
        current_language = selected_lang
        load_language(current_language)
        update_ui_language()

language_var = tk.StringVar(value=current_language)  # Set default language
language_dropdown = ttk.Combobox(left_frame_bottom, textvariable=language_var, values=["ru", "en"], state="readonly", width=10)
language_dropdown.pack(pady=5)
language_dropdown.bind("<<ComboboxSelected>>", change_language)

# Function to update UI elements based on selected language
def update_ui_language():
    root.title(translate("title"))
    
    # Update labels and buttons in left_frame_top
    select_folder_label.config(text=translate("select_folder"))
    select_folder_button.config(text=translate("select_plugin_folder"))
    select_zip_button.config(text=translate("select_zip"))
    
    # Update labels and buttons in left_frame_bottom
    select_version_label.config(text=translate("select_engine_version"))
    rebuild_button.config(text=translate("rebuild_plugins"))
    open_folder_button.config(text=translate("open_build_folder"))
    
    # Update other UI elements as necessary
    # For example, delete buttons in plugins are already updated through the logging system

# Handle window closing
root.protocol("WM_DELETE_WINDOW", on_closing)
plugins_inner_frame.bind("<Configure>", on_frame_configure)
plugins_canvas.bind('<Configure>', on_canvas_configure)
root.bind_all("<MouseWheel>", on_mousewheel)
engine_dropdown.bind("<<ComboboxSelected>>", on_selected_engine)
# Call the separator function after setting up logging
log_separator()

# Delete the RebuiltPlugins folder on startup
delete_rebuilt_plugins_folder()

# Start the application and log the start message
log_message("app_started", "user", "info")
root.mainloop()
