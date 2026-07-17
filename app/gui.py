"""
The GUI. A proper resizable main window that opens when the exe runs,
instead of a hidden tray-only app. Layout:

  - Top bar: "Process now" button + status line (always visible)
  - Tabs: Upload | Search | Settings | Log
  - Bottom bar: which photo is currently being processed, if any

Closing the window (the X button) quits the app entirely. This does NOT
affect the nightly automatic run -- that's a separate Windows Scheduled
Task that calls the exe directly with --run-once, independent of whether
this window or its tray icon is running. A tray icon is still shown while
the app is open, purely as a shortcut for "Process now" without switching
windows; closing from either the window or the tray's Quit item exits the
whole app the same way.

Heavy imports (torch, open_clip, bioclip) only happen inside pipeline
functions when actually processing -- so opening this window is instant.
"""

import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pystray
from PIL import Image, ImageDraw, ImageTk

from . import config, metadata, pipeline, scheduler, search

logger = logging.getLogger("wildlifetagger.gui")

LOG_FILE = Path.home() / "WildlifeTagger.log"


def _tray_icon_image():
    """A plain placeholder icon; swap for a real .ico in the installer."""
    img = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(60, 130, 80))
    return img


class MainWindow(tk.Tk):
    def __init__(self, exe_path: str):
        super().__init__()
        self.exe_path = exe_path
        self.settings = config.load_settings()
        config.ensure_folders(self.settings)

        self.title("Wildlife Tagger")
        self.geometry("640x480")
        self.minsize(520, 400)
        self.resizable(True, True)  # explicit: this window can be resized

        self.tray_icon = None
        # Closing the window quits the app outright. The nightly run doesn't
        # need this process alive -- the Scheduled Task calls the exe
        # directly with --run-once -- so there's no reason to hide the app
        # in the tray and leave someone wondering why it's still running.
        self.protocol("WM_DELETE_WINDOW", self._quit)

        self._build_menu()
        self._build_layout()
        self._start_tray()

    # ---------- layout ----------

    def _build_menu(self):
        menubar = tk.Menu(self)
        app_menu = tk.Menu(menubar, tearoff=False)
        app_menu.add_command(label="Quit", command=self._quit)
        menubar.add_cascade(label="File", menu=app_menu)
        self.config(menu=menubar)

    def _build_layout(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        top_bar = ttk.Frame(self, padding=10)
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.grid_columnconfigure(1, weight=1)

        ttk.Button(top_bar, text="Process now", command=self._process_now).grid(
            row=0, column=0, padx=(0, 12)
        )
        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(top_bar, textvariable=self.status_var).grid(row=0, column=1, sticky="w")

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        self.upload_tab = ttk.Frame(notebook, padding=16)
        self.search_tab = ttk.Frame(notebook, padding=16)
        self.settings_tab = ttk.Frame(notebook, padding=16)
        self.log_tab = ttk.Frame(notebook, padding=16)

        notebook.add(self.upload_tab, text="Upload")
        notebook.add(self.search_tab, text="Search")
        notebook.add(self.settings_tab, text="Settings")
        notebook.add(self.log_tab, text="Log")

        self._build_upload_tab()
        self._build_search_tab()
        self._build_settings_tab()
        self._build_log_tab()

        # Persistent bottom status bar -- shows which photo is currently
        # being processed, visible no matter which tab is open.
        bottom_bar = ttk.Frame(self, padding=(10, 6))
        bottom_bar.grid(row=2, column=0, sticky="ew")
        self.current_file_var = tk.StringVar(value="")
        ttk.Label(bottom_bar, textvariable=self.current_file_var, foreground="gray").grid(
            row=0, column=0, sticky="w"
        )

    def _build_upload_tab(self):
        tab = self.upload_tab
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(4, weight=1)  # preview area expands into the blank space below

        ttk.Label(tab, text="Additional Text (optional, applies to this whole batch):").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        self.name_entry = ttk.Entry(tab)
        self.name_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        self.selected_files = []
        self.file_label = ttk.Label(tab, text="No files selected", foreground="gray")
        self.file_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Button(tab, text="Choose photos...", command=self._pick_files).grid(
            row=3, column=0, sticky="w"
        )
        ttk.Button(tab, text="Upload", command=self._upload).grid(row=3, column=1, sticky="e")

        # Live preview of whatever photo is currently being processed --
        # fills the remaining blank space below the controls above.
        preview_frame = ttk.Frame(tab, relief="groove", borderwidth=1)
        preview_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(16, 0))
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)

        self._preview_photo = None  # keep a reference so tkinter doesn't garbage-collect it
        self.preview_label = ttk.Label(
            preview_frame,
            text="No photo currently processing.",
            foreground="gray",
            anchor="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        preview_frame.bind("<Configure>", self._on_preview_frame_resized)
        self._last_preview_path = None

    def _build_search_tab(self):
        tab = self.search_tab
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        ttk.Label(tab, text="Folder to search:").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.search_folder_entry = ttk.Entry(tab)
        self.search_folder_entry.insert(0, self.settings["output_folder"])
        self.search_folder_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=(0, 4))

        def browse_search_folder():
            chosen = filedialog.askdirectory(initialdir=self.search_folder_entry.get() or ".")
            if chosen:
                self.search_folder_entry.delete(0, tk.END)
                self.search_folder_entry.insert(0, chosen)

        ttk.Button(tab, text="Browse...", command=browse_search_folder).grid(row=0, column=2)

        ttk.Label(tab, text="Search tags (space-separated, matches any):").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(12, 4)
        )
        self.search_query_entry = ttk.Entry(tab)
        self.search_query_entry.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        self.search_query_entry.bind("<Return>", lambda _: self._run_search())
        ttk.Button(tab, text="Search", command=self._run_search).grid(row=2, column=2, sticky="ew")

        results_frame = ttk.Frame(tab)
        results_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(12, 8))
        results_frame.grid_rowconfigure(0, weight=1)
        results_frame.grid_columnconfigure(0, weight=1)

        self.search_listbox = tk.Listbox(results_frame)
        self.search_listbox.grid(row=0, column=0, sticky="nsew")
        results_scrollbar = ttk.Scrollbar(results_frame, command=self.search_listbox.yview)
        results_scrollbar.grid(row=0, column=1, sticky="ns")
        self.search_listbox.config(yscrollcommand=results_scrollbar.set)

        self.search_status_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.search_status_var, foreground="gray").grid(
            row=4, column=0, columnspan=3, sticky="w"
        )

        ttk.Button(tab, text="Open selected photo", command=self._open_selected_result).grid(
            row=5, column=0, sticky="w", pady=(8, 0)
        )

        ttk.Label(tab, text="Add tags to selected photo:").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(16, 4)
        )
        self.add_tags_entry = ttk.Entry(tab)
        self.add_tags_entry.grid(row=7, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        ttk.Button(tab, text="Add tags", command=self._add_tags_to_selected).grid(
            row=7, column=2, sticky="ew"
        )

        self._search_results: list = []

    def _build_settings_tab(self):
        tab = self.settings_tab
        tab.grid_columnconfigure(1, weight=1)
        s = self.settings

        self.inbox_entry = self._folder_row(tab, "Inbox folder:", s["inbox_folder"], 0)
        self.output_entry = self._folder_row(
            tab, "Output folder (review/human/scenery live inside this):", s["output_folder"], 1
        )

        ttk.Label(tab, text="Nightly run time (24h, HH:MM):").grid(
            row=2, column=0, sticky="w", pady=(16, 4)
        )
        self.time_entry = ttk.Entry(tab, width=10)
        self.time_entry.insert(0, s["nightly_run_time"])
        self.time_entry.grid(row=2, column=1, sticky="w", pady=(16, 4))

        ttk.Label(tab, text="Species confidence threshold (%):").grid(
            row=3, column=0, sticky="w", pady=4
        )
        self.conf_entry = ttk.Entry(tab, width=10)
        self.conf_entry.insert(0, str(int(s["species_confidence_threshold"] * 100)))
        self.conf_entry.grid(row=3, column=1, sticky="w", pady=4)

        self.keep_backup_var = tk.BooleanVar(value=s.get("keep_backup", False))
        ttk.Checkbutton(
            tab,
            text="Keep a backup copy of original uploads (in inbox/processed/)",
            variable=self.keep_backup_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 4))

        ttk.Button(tab, text="Save settings", command=self._save_settings).grid(
            row=5, column=0, columnspan=2, pady=20
        )

    def _folder_row(self, tab, label, value, row):
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=(0 if row else 4, 4))
        entry = ttk.Entry(tab)
        entry.insert(0, value)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8))

        def browse():
            chosen = filedialog.askdirectory(initialdir=entry.get() or ".")
            if chosen:
                entry.delete(0, tk.END)
                entry.insert(0, chosen)

        ttk.Button(tab, text="Browse...", command=browse).grid(row=row, column=2)
        return entry

    def _build_log_tab(self):
        tab = self.log_tab
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(tab, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=scrollbar.set)

        ttk.Button(tab, text="Refresh", command=self._refresh_log).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        self._refresh_log()

    def _refresh_log(self):
        self.log_text.delete("1.0", tk.END)
        if LOG_FILE.exists():
            self.log_text.insert(tk.END, LOG_FILE.read_text(encoding="utf-8", errors="replace"))
            self.log_text.see(tk.END)
        else:
            self.log_text.insert(tk.END, "No log entries yet.")

    # ---------- actions ----------

    def _pick_files(self):
        files = filedialog.askopenfilenames(
            title="Select photos",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.heic")],
        )
        if files:
            self.selected_files = list(files)
            self.file_label.config(text=f"{len(files)} photo(s) selected", foreground="black")

    def _upload(self):
        name = self.name_entry.get().strip()
        if not self.selected_files:
            messagebox.showwarning("Wildlife Tagger", "Please choose at least one photo.")
            return

        inbox = Path(self.settings["inbox_folder"])
        inbox.mkdir(parents=True, exist_ok=True)

        copied_names = []
        for src in self.selected_files:
            src_path = Path(src)
            dest = inbox / src_path.name
            dest.write_bytes(src_path.read_bytes())
            copied_names.append(dest.name)

        pipeline.register_upload(inbox, copied_names, name)
        who = f" for {name}" if name else ""
        messagebox.showinfo(
            "Wildlife Tagger",
            f"{len(copied_names)} photo(s) uploaded{who}.\n"
            "They'll be processed tonight, or click 'Process now' above.",
        )
        self.selected_files = []
        self.file_label.config(text="No files selected", foreground="gray")
        self.name_entry.delete(0, tk.END)

    def _run_search(self):
        folder_str = self.search_folder_entry.get().strip()
        if not folder_str:
            messagebox.showwarning("Wildlife Tagger", "Please choose a folder to search.")
            return

        folder = Path(folder_str)
        if not folder.exists():
            messagebox.showwarning("Wildlife Tagger", f"Folder not found:\n{folder}")
            return

        query = self.search_query_entry.get()
        self.search_status_var.set("Searching...")
        self.search_listbox.delete(0, tk.END)

        def worker():
            results = search.search_folder(folder, query, match_all=False)

            def finish():
                self._search_results = results
                for path in results:
                    self.search_listbox.insert(tk.END, path.name)
                count = len(results)
                self.search_status_var.set(
                    f"{count} photo(s) found." if query.strip() else f"{count} photo(s) in folder."
                )

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _selected_result_path(self):
        selection = self.search_listbox.curselection()
        if not selection:
            messagebox.showinfo("Wildlife Tagger", "Select a photo in the results list first.")
            return None
        return self._search_results[selection[0]]

    def _open_selected_result(self):
        path = self._selected_result_path()
        if path:
            os.startfile(str(path))  # Windows-only, matches target platform

    def _add_tags_to_selected(self):
        path = self._selected_result_path()
        if not path:
            return

        new_tags = self.add_tags_entry.get().strip().split()
        if not new_tags:
            messagebox.showwarning("Wildlife Tagger", "Type at least one tag to add.")
            return

        ok = metadata.append_tags(path, new_tags)
        if ok:
            messagebox.showinfo("Wildlife Tagger", f"Tags added to {path.name}.")
            self.add_tags_entry.delete(0, tk.END)
        else:
            messagebox.showwarning(
                "Wildlife Tagger",
                "Couldn't write tags -- check that exiftool.exe is installed "
                "(see the README's setup section).",
            )

    def _on_preview_frame_resized(self, event):
        if self._last_preview_path is not None:
            self._render_preview_image(self._last_preview_path, event.width, event.height)

    def _update_preview(self, image_path: Path):
        self._last_preview_path = image_path
        frame = self.preview_label.master
        width = frame.winfo_width() or 400
        height = frame.winfo_height() or 300
        self._render_preview_image(image_path, width, height)

    def _render_preview_image(self, image_path: Path, frame_width: int, frame_height: int):
        try:
            img = Image.open(image_path)
            img = img.convert("RGB")
            # Small margin so the thumbnail doesn't touch the frame's border.
            max_size = (max(frame_width - 20, 50), max(frame_height - 20, 50))
            img.thumbnail(max_size)
            photo = ImageTk.PhotoImage(img)
            self._preview_photo = photo  # keep a reference -- tkinter won't hold one itself
            self.preview_label.config(image=photo, text="")
        except Exception:
            # Corrupt/unreadable file, or the file already moved on by the
            # time we got here -- not worth failing processing over, just
            # show a placeholder instead of a thumbnail.
            self._preview_photo = None
            self.preview_label.config(
                image="", text=f"Preview unavailable for {Path(image_path).name}"
            )

    def _clear_preview(self):
        self._last_preview_path = None
        self._preview_photo = None
        self.preview_label.config(image="", text="No photo currently processing.")

    def _process_now(self):
        base_message = "Processing... this can take a while on the first run."
        self.status_var.set(base_message)
        self.current_file_var.set("")

        def on_progress(index, total, filename):
            def update():
                self.status_var.set(f"{base_message} ({index}/{total})")
                self.current_file_var.set(f"Currently processing: {filename}")
                # The file still sits in the inbox at this point -- process_one
                # hasn't moved it yet -- so it's safe to read for a preview.
                self._update_preview(Path(self.settings["inbox_folder"]) / filename)

            self.after(0, update)

        def worker():
            results = pipeline.process_inbox(self.settings, progress_callback=on_progress)
            ok = sum(1 for r in results if r.ok)
            reviewed = sum(1 for r in results if r.ok and r.needs_review)
            failed = [r for r in results if not r.ok]

            def finish():
                summary = f"Done. Processed {ok} photo(s), {len(failed)} failed."
                if reviewed:
                    summary += f" {reviewed} sent to review."
                self.status_var.set(summary)
                self.current_file_var.set("")
                self._clear_preview()
                self._refresh_log()
                if failed:
                    for r in failed:
                        logger.error("Failed: %s -- %s", r.original_path, r.error)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _save_settings(self):
        s = self.settings
        s["inbox_folder"] = self.inbox_entry.get().strip()
        s["output_folder"] = self.output_entry.get().strip()

        new_time = self.time_entry.get().strip()
        try:
            hh, mm = new_time.split(":")
            assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
        except Exception:
            messagebox.showerror("Wildlife Tagger", "Please enter time as HH:MM, e.g. 23:00")
            return

        try:
            conf_pct = float(self.conf_entry.get().strip())
            assert 0 <= conf_pct <= 100
        except Exception:
            messagebox.showerror("Wildlife Tagger", "Confidence must be a number 0-100.")
            return

        time_changed = new_time != s["nightly_run_time"]
        s["nightly_run_time"] = new_time
        s["species_confidence_threshold"] = conf_pct / 100.0
        s["keep_backup"] = self.keep_backup_var.get()

        config.save_settings(s)
        config.ensure_folders(s)

        if time_changed:
            ok, msg = scheduler.register_nightly_task(self.exe_path, new_time)
            if not ok:
                messagebox.showwarning(
                    "Wildlife Tagger",
                    f"Settings saved, but couldn't update the nightly schedule:\n{msg}\n"
                    "You may need to run the app as administrator once to fix this.",
                )
                return

        messagebox.showinfo("Wildlife Tagger", "Settings saved.")

    # ---------- tray ----------

    def _start_tray(self):
        self.tray_icon = pystray.Icon(
            "WildlifeTagger",
            _tray_icon_image(),
            "Wildlife Tagger",
            menu=pystray.Menu(
                pystray.MenuItem("Show window", lambda: self.after(0, self._restore)),
                pystray.MenuItem("Process now", lambda: self.after(0, self._process_now)),
                pystray.MenuItem("Quit", lambda: self.after(0, self._quit)),
            ),
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _restore(self):
        self.deiconify()
        self.lift()

    def _quit(self):
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()


def run_app(exe_path: str):
    window = MainWindow(exe_path)
    window.mainloop()
