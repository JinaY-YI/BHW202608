"""
运行日志Tab：显示系统日志
"""
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox


class LogViewTab:
    """运行日志Tab"""

    def __init__(self, notebook, log_callback=None):
        self.log_callback = log_callback

        parent = tk.Frame(notebook, bg='#0d1117')
        notebook.add(parent, text="📝 运行日志")
        self.parent = parent

        self._build()

    def _build(self):
        toolbar = tk.Frame(self.parent, bg='#0d1117')
        toolbar.pack(fill=tk.X, pady=5, padx=5)

        tk.Button(toolbar, text="🗑 清空", command=self._clear,
                  bg='#30363d', fg='#f0f6fc', font=('微软雅黑', 9), padx=10).pack(side=tk.LEFT)
        tk.Button(toolbar, text="📥 导出", command=self._export,
                  bg='#30363d', fg='#f0f6fc', font=('微软雅黑', 9), padx=10).pack(side=tk.LEFT, padx=5)

        self.text = scrolledtext.ScrolledText(self.parent, bg='#0d1117', fg='#58a6ff',
                                              font=('Consolas', 9), insertbackground='white',
                                              relief=tk.FLAT, bd=0)
        self.text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.text.insert(tk.END, "[系统] 电网模拟器启动完成，等待连接...\n")
        self.text.see(tk.END)

    def log(self, msg, level="INFO"):
        """写入日志（线程安全：由主线程调用）"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌"}.get(level, "📌")
        line = f"[{timestamp}] {prefix} {msg}\n"
        self.text.insert(tk.END, line)
        self.text.see(tk.END)

    def _clear(self):
        self.text.delete(1.0, tk.END)

    def _export(self):
        filepath = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文本文件", "*.txt")])
        if filepath:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(self.text.get(1.0, tk.END))
            messagebox.showinfo("导出成功", "日志已导出")