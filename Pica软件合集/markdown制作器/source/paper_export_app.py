"""Windows desktop UI and command line for the text-layer Markdown exporter."""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from paper_export import convert_pdf


def enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except AttributeError:
        pass


def selected_pdfs(path):
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() == ".pdf" else []
    if not path.is_dir():
        return []
    return sorted(
        (file for file in path.rglob("*") if file.is_file() and file.suffix.lower() == ".pdf"),
        key=lambda item: str(item).casefold(),
    )


def run_batch(files, output, report):
    successful = 0
    for index, pdf in enumerate(files, 1):
        report(f"[{index}/{len(files)}] {pdf.name}")
        try:
            result = convert_pdf(pdf, output)
        except (OSError, RuntimeError, ValueError) as error:
            report(f"  失败 · {error}")
        else:
            successful += 1
            report(f"  已生成 · {result.path.name}（{result.page_count} 页）")
            if result.ignored_footers:
                report(f"  已忽略 {result.ignored_footers} 条推广页脚或第三方听力提示")
            for warning in result.warnings:
                report(f"  核对提示 · {warning}")
    return successful


class ExportWindow:
    def __init__(self, root):
        self.root = root
        root.title("PicaReader 原卷 Markdown 制作工具")
        self.files = []
        self.running = False
        self.events = Queue()
        self.dpi = None
        self.style = ttk.Style(root)
        self.heading_font = font.nametofont("TkDefaultFont").copy()
        self.heading_font.configure(size=18, weight="bold")
        self.body_font = font.nametofont("TkDefaultFont")
        default_output = Path("D:/desk/Codex Operate files")
        self.text = tk.StringVar(value="请选择 PDF 文件或文件夹")
        self.output = tk.StringVar(value=str(default_output if default_output.is_dir() else Path.home() / "Documents"))
        self.status = tk.StringVar(value="等待选择 PDF")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.main = ttk.Frame(root)
        self.main.grid(sticky="nsew")
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(6, weight=1)

        ttk.Label(self.main, text="原卷 Markdown 制作工具", font=self.heading_font).grid(row=0, column=0, sticky="w")
        ttk.Label(self.main, text="读取 PDF 已有文字层，输出原卷正文和学习附录占位。").grid(row=1, column=0, sticky="w")
        source = ttk.Frame(self.main)
        source.grid(row=2, column=0, sticky="ew")
        source.columnconfigure(0, weight=1)
        ttk.Entry(source, textvariable=self.text, state="readonly").grid(row=0, column=0, sticky="ew")
        self.browse_files = ttk.Button(source, text="选择 PDF", command=self.pick_files)
        self.browse_files.grid(row=0, column=1, padx=(8, 0))
        self.browse_folder = ttk.Button(source, text="选择文件夹", command=self.pick_folder)
        self.browse_folder.grid(row=0, column=2, padx=(8, 0))

        ttk.Label(self.main, text="保存位置").grid(row=3, column=0, sticky="w")
        destination = ttk.Frame(self.main)
        destination.grid(row=4, column=0, sticky="ew")
        destination.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(destination, textvariable=self.output)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.browse_output = ttk.Button(destination, text="浏览", command=self.pick_output)
        self.browse_output.grid(row=0, column=1, padx=(8, 0))

        actions = ttk.Frame(self.main)
        actions.grid(row=5, column=0, sticky="ew")
        actions.columnconfigure(2, weight=1)
        self.start = ttk.Button(actions, text="生成 Markdown", command=self.begin, state="disabled")
        self.start.grid(row=0, column=0)
        ttk.Button(actions, text="打开输出目录", command=self.open_output).grid(row=0, column=1, padx=8)
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=2, sticky="e")

        log = ttk.Frame(self.main)
        log.grid(row=6, column=0, sticky="nsew")
        log.rowconfigure(0, weight=1)
        log.columnconfigure(0, weight=1)
        self.log = tk.Text(log, width=1, height=1, wrap="word", padx=8, pady=8,
                           font=self.body_font, state="disabled", takefocus=True)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self.controls = (self.browse_files, self.browse_folder, self.output_entry, self.browse_output, self.start)

        self.apply_scale(round(root.winfo_fpixels("1i")), initial=True)
        root.bind("<Configure>", self.check_scale)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def apply_scale(self, dpi, initial=False):
        if dpi == self.dpi:
            return
        self.dpi = dpi
        self.root.tk.call("tk", "scaling", dpi / 72)
        ratio = dpi / 96
        self.main.configure(padding=round(20 * ratio))
        self.style.configure("TButton", padding=(round(9 * ratio), round(5 * ratio)))
        for row in (1, 2, 4, 5):
            self.main.grid_slaves(row=row)[0].grid_configure(pady=(round(5 * ratio), round(10 * ratio)))
        for name in font.names(self.root):
            named = font.nametofont(name, root=self.root)
            named.configure(size=named.cget("size"))
        self.root.update_idletasks()
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.minsize(min(round(590 * ratio), round(width * 0.92)),
                          min(round(345 * ratio), round(height * 0.86)))
        if initial:
            self.root.geometry(f"{min(round(820 * ratio), round(width * 0.92))}x{min(round(490 * ratio), round(height * 0.85))}")

    def check_scale(self, event):
        if event.widget is self.root and sys.platform == "win32":
            try:
                get_dpi = ctypes.windll.user32.GetDpiForWindow
                get_dpi.argtypes = (ctypes.c_void_p,)
                get_dpi.restype = ctypes.c_uint
                dpi = get_dpi(self.root.winfo_id())
                if dpi and dpi != self.dpi:
                    self.root.after_idle(self.apply_scale, dpi)
            except AttributeError:
                pass

    def set_files(self, files, label):
        self.files = files
        self.text.set(label)
        self.status.set(f"已选择 {len(files)} 份 PDF" if files else "未找到 PDF")
        self.start.configure(state="normal" if files else "disabled")

    def pick_files(self):
        choices = filedialog.askopenfilenames(parent=self.root, title="选择 PDF", filetypes=[("PDF", "*.pdf")])
        if choices:
            files = [Path(name) for name in choices]
            self.set_files(files, files[0].name if len(files) == 1 else f"{files[0].name} 等 {len(files)} 份 PDF")

    def pick_folder(self):
        choice = filedialog.askdirectory(parent=self.root, title="选择 PDF 文件夹")
        if choice:
            self.set_files(selected_pdfs(choice), choice)

    def pick_output(self):
        choice = filedialog.askdirectory(parent=self.root, title="选择 Markdown 保存位置")
        if choice:
            self.output.set(choice)

    def open_output(self):
        folder = Path(self.output.get())
        if sys.platform == "win32" and folder.is_dir():
            os.startfile(folder)
        else:
            messagebox.showinfo("保存位置", str(folder), parent=self.root)

    def append_log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def begin(self):
        if self.running or not self.files:
            return
        output = self.output.get().strip()
        if not output:
            messagebox.showerror("请选择保存位置", "请选择保存 Markdown 的文件夹。", parent=self.root)
            return
        self.running = True
        files = self.files[:]
        for widget in self.controls:
            widget.configure(state="disabled")
        self.status.set(f"正在转换 {len(files)} 份 PDF")
        self.append_log(f"保存位置 · {output}")

        def work():
            try:
                success = run_batch(files, Path(output), lambda message: self.events.put(("log", message)))
                summary = f"已生成 {success} / {len(files)} 份"
            except Exception as error:
                self.events.put(("log", f"转换中断 · {error}"))
                summary = "转换中断"
            self.events.put(("done", summary))

        Thread(target=work, daemon=True).start()
        self.root.after(50, self.read_events)

    def read_events(self):
        while True:
            try:
                kind, message = self.events.get_nowait()
            except Empty:
                break
            if kind == "done":
                self.running = False
                self.status.set(message)
                for widget in self.controls:
                    widget.configure(state="normal")
            self.append_log(message)
        if self.running:
            self.root.after(50, self.read_events)

    def close(self):
        if self.running:
            messagebox.showinfo("正在转换", "请等这一批文件转换完成后再关闭。", parent=self.root)
            return
        self.root.destroy()


def main():
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="读取已有文字层，生成原卷和附录组成的 Markdown")
        parser.add_argument("input", help="单个 PDF 或文件夹")
        parser.add_argument("output", help="保存目录")
        args = parser.parse_args()
        files = selected_pdfs(args.input)
        if not files:
            parser.error("未找到 PDF 文件")
        return 0 if run_batch(files, Path(args.output), print) == len(files) else 1

    enable_dpi_awareness()
    root = tk.Tk()
    icon = tk.PhotoImage(master=root, file=str(Path(__file__).with_name("pica-icon.png")))
    root.iconphoto(True, icon.subsample(4))
    ExportWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

