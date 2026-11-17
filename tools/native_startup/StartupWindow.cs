// An early Windows splash, independent of Python imports and EXE unpacking.
// Native acrylic follows SADM Autorouter's Accent/DWM approach.
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.IO;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal sealed class StartupWindow : Form
{
    const int LogicalWidth = 560, LogicalHeight = 312;
    readonly Bitmap artwork;
    readonly Bitmap[] water = new Bitmap[48];
    readonly System.Windows.Forms.Timer animation = new System.Windows.Forms.Timer();
    readonly Font percentFont = new Font("Segoe UI Light", 62, FontStyle.Regular, GraphicsUnit.Pixel);
    readonly Font statusFont = new Font("Segoe UI", 13, FontStyle.Regular, GraphicsUnit.Pixel);
    readonly Font footerFont = new Font("Segoe UI", 10, FontStyle.Regular, GraphicsUnit.Pixel);
    readonly RectangleF minimize = new RectangleF(508, 18, 32, 28);
    readonly object writeLock = new object();
    readonly int port, parentId;
    readonly string token;
    readonly float scale;
    volatile int progress;
    volatile string message = "Preparing application...";
    volatile bool finishing;
    StreamWriter writer;
    TcpClient link;
    int frame;
    bool hover, pressed;
    int glassMode;

    [STAThread]
    static int Main(string[] args)
    {
        try
        {
            if (args.Length != 3) return 2;
            SetProcessDPIAware();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (StartupWindow window = new StartupWindow(int.Parse(args[0]), args[1], int.Parse(args[2])))
                Application.Run(window);
            return 0;
        }
        catch { return 2; } // The bootloader retains a functional fallback.
    }

    StartupWindow(int controlPort, string controlToken, int parent)
    {
        port = controlPort; token = controlToken; parentId = parent;
        using (Graphics screen = Graphics.FromHwnd(IntPtr.Zero)) scale = screen.DpiX / 96f;
        AutoScaleMode = AutoScaleMode.None;
        ClientSize = new Size((int)(LogicalWidth*scale), (int)(LogicalHeight*scale));
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = true;
        MinimizeBox = true;
        MaximizeBox = false;
        TopMost = true;
        StartPosition = FormStartPosition.CenterScreen;
        Text = "PLR000-CCA001 Extractor - Starting";
        AccessibleName = "PLR000-CCA001 startup progress";
        Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location);
        artwork = ReadBitmap("startup_artwork");
        for (int i = 0; i < water.Length; i++) water[i] = ReadBitmap("water_"+i.ToString("000"));
        animation.Interval = 40;
        animation.Tick += delegate {
            SetProp(Handle, "PLRStartupProgress", new IntPtr(progress+1));
            if (!IsIconic(Handle) && WindowState != FormWindowState.Minimized) { frame = (frame+1)%water.Length; Render(); }
        };
    }

    static Bitmap ReadBitmap(string name)
    {
        using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
        using (Bitmap source = new Bitmap(stream)) return new Bitmap(source);
    }

    protected override CreateParams CreateParams
    {
        get {
            CreateParams value = base.CreateParams;
            value.ExStyle |= 0x00080000 | 0x00040000; // Layered, real taskbar entry.
            value.Style |= 0x00020000 | 0x00080000; // Minimize box and system menu.
            return value;
        }
    }

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        // .NET Framework can adjust the initial size during its first DPI/font
        // transition. Set the full logical card size after that transition.
        ClientSize = new Size((int)(LogicalWidth*scale), (int)(LogicalHeight*scale));
        CenterToScreen();
        glassMode = ApplyGlass(Handle);
        Render();
        SetProp(Handle, "PLRStartupProgress", new IntPtr(progress+1));
        SetProp(Handle, "PLRGlassMode", new IntPtr(glassMode));
        animation.Start();
        new Thread(ControlLoop) { IsBackground = true, Name = "Startup status" }.Start();
        new Thread(delegate() {
            try { using (Process parent = Process.GetProcessById(parentId)) parent.WaitForExit(); }
            catch (ArgumentException) { }
            Shutdown();
        }) { IsBackground = true, Name = "Startup lifetime" }.Start();
    }

    protected override void OnPaint(PaintEventArgs e) { }
    protected override void OnPaintBackground(PaintEventArgs e) { }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        SendState();
        if (artwork != null && !IsIconic(Handle) && WindowState != FormWindowState.Minimized) Render();
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        hover = minimize.Contains(e.X/scale, e.Y/scale);
        Cursor = hover ? Cursors.Hand : Cursors.Default;
    }

    protected override void OnMouseLeave(EventArgs e) { base.OnMouseLeave(e); hover = false; }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        if (e.Button != MouseButtons.Left) return;
        if (minimize.Contains(e.X/scale, e.Y/scale)) {
            hover = true; pressed = true; Capture = true; Render(); return;
        }
        ReleaseCapture();
        SendMessage(Handle, 0x00A1, new IntPtr(2), IntPtr.Zero); // Drag the frameless card.
    }

    protected override void OnMouseUp(MouseEventArgs e)
    {
        base.OnMouseUp(e);
        if (e.Button != MouseButtons.Left || !pressed) return;
        bool minimizeNow = minimize.Contains(e.X/scale, e.Y/scale);
        pressed = false; Capture = false; hover = false;
        if (minimizeNow) { WindowState = FormWindowState.Minimized; SendState(); }
        else Render();
    }

    static GraphicsPath RoundedButton(RectangleF rect)
    {
        const float diameter = 14; // Match the card's softer seven-pixel control radius.
        GraphicsPath path = new GraphicsPath();
        path.AddArc(rect.Left, rect.Top, diameter, diameter, 180, 90);
        path.AddArc(rect.Right-diameter, rect.Top, diameter, diameter, 270, 90);
        path.AddArc(rect.Right-diameter, rect.Bottom-diameter, diameter, diameter, 0, 90);
        path.AddArc(rect.Left, rect.Bottom-diameter, diameter, diameter, 90, 90);
        path.CloseFigure();
        return path;
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!finishing && e.CloseReason == CloseReason.UserClosing) {
            e.Cancel = true; WindowState = FormWindowState.Minimized;
        }
        base.OnFormClosing(e);
    }

    protected override AccessibleObject CreateAccessibilityInstance() { return new CardAccessibility(this); }
    sealed class CardAccessibility : ControlAccessibleObject
    {
        readonly StartupWindow owner;
        public CardAccessibility(StartupWindow window) : base(window) { owner = window; }
        public override int GetChildCount() { return 1; }
        public override AccessibleObject GetChild(int index) { return index == 0 ? new MinimizeAccessibility(owner) : null; }
    }
    sealed class MinimizeAccessibility : AccessibleObject
    {
        readonly StartupWindow owner;
        public MinimizeAccessibility(StartupWindow window) { owner = window; }
        public override string Name { get { return "Minimize - startup will continue"; } }
        public override AccessibleRole Role { get { return AccessibleRole.PushButton; } }
        public override string DefaultAction { get { return "Minimize"; } }
        public override Rectangle Bounds { get { return owner.RectangleToScreen(new Rectangle((int)(508*owner.scale), (int)(18*owner.scale), (int)(32*owner.scale), (int)(28*owner.scale))); } }
        public override void DoDefaultAction() { owner.WindowState = FormWindowState.Minimized; }
    }

    void Render()
    {
        if (!IsHandleCreated || IsDisposed || IsIconic(Handle) || WindowState == FormWindowState.Minimized) return;
        using (Bitmap surface = new Bitmap(ClientSize.Width, ClientSize.Height, PixelFormat.Format32bppArgb))
        {
            using (Graphics g = Graphics.FromImage(surface))
            {
                g.ScaleTransform(scale, scale);
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
                g.DrawImage(artwork, new Rectangle(0, 0, LogicalWidth, LogicalHeight));
                if (glassMode == 0) using (Brush tint = new SolidBrush(Color.FromArgb(55, 9, 18, 33)))
                    g.FillRectangle(tint, 1, 1, LogicalWidth-2, LogicalHeight-2);
                g.DrawImage(water[frame], new Rectangle(12, 164, 536, 136));
                using (Brush text = new SolidBrush(Color.FromArgb(238, 251, 255)))
                    g.DrawString(progress+"%", percentFont, text, new PointF(30, 61), StringFormat.GenericTypographic);
                using (Brush text = new SolidBrush(Color.FromArgb(212, 230, 244)))
                    g.DrawString(message, statusFont, text, new RectangleF(220, 135, 300, 34));
                using (Brush text = new SolidBrush(Color.FromArgb(155, 186, 203)))
                    g.DrawString("This window closes when your workspace is ready.", footerFont, text, new PointF(36, 279));
                if (hover) using (GraphicsPath button = RoundedButton(minimize)) {
                    using (Brush tint = new SolidBrush(Color.FromArgb(pressed ? 48 : 28, 175, 222, 239)))
                        g.FillPath(tint, button);
                    using (Pen rim = new Pen(Color.FromArgb(pressed ? 70 : 38, 156, 221, 235), 1))
                        g.DrawPath(rim, button);
                }
                using (Pen pen = new Pen(Color.FromArgb(190, 215, 229), 1.3f)) g.DrawLine(pen, 519, 32, 530, 32);
            }
            IntPtr screen = GetDC(IntPtr.Zero), memory = CreateCompatibleDC(screen);
            IntPtr bitmap = surface.GetHbitmap(Color.FromArgb(0)), previous = SelectObject(memory, bitmap);
            try {
                Point target = Location, source = Point.Empty;
                Size size = surface.Size;
                Blend blend = new Blend { Operation = 0, Flags = 0, Alpha = 255, Format = 1 };
                // A minimize message can arrive while the bitmap is being
                // composed. Updating a layered iconic HWND would restore it.
                if (!IsIconic(Handle) && WindowState != FormWindowState.Minimized)
                    UpdateLayeredWindow(Handle, screen, ref target, ref size, memory, ref source, 0, ref blend, 2);
            }
            finally { SelectObject(memory, previous); DeleteObject(bitmap); DeleteDC(memory); ReleaseDC(IntPtr.Zero, screen); }
        }
    }

    void ControlLoop()
    {
        try
        {
            using (TcpClient client = new TcpClient("127.0.0.1", port))
            using (StreamReader reader = new StreamReader(client.GetStream(), Encoding.UTF8))
            using (StreamWriter output = new StreamWriter(client.GetStream(), new UTF8Encoding(false)))
            {
                client.NoDelay = true; output.NewLine = "\n"; output.AutoFlush = true; link = client;
                lock (writeLock) { writer = output; output.WriteLine("HELLO\t"+token+"\t"+glassMode); }
                BeginInvoke((Action)SendState);
                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    string[] parts = line.Split('\t');
                    int value;
                    if (parts.Length == 3 && parts[0] == "PROGRESS" && int.TryParse(parts[1], out value)) {
                        message = Encoding.UTF8.GetString(Convert.FromBase64String(parts[2]));
                        progress = Math.Max(progress, Math.Min(100, Math.Max(0, value)));
                    }
                }
            }
        }
        catch (Exception) { }
        finally { lock (writeLock) writer = null; Shutdown(); }
    }

    void SendState()
    {
        try { lock (writeLock) if (writer != null) writer.WriteLine("STATE\t"+(IsIconic(Handle) ? "minimized" : "visible")); }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
    }

    void Shutdown()
    {
        if (IsDisposed || !IsHandleCreated) return;
        try { BeginInvoke((Action)delegate { finishing = true; Close(); }); }
        catch (InvalidOperationException) { }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) {
            finishing = true; animation.Dispose(); if (link != null) link.Close();
            if (artwork != null) artwork.Dispose();
            foreach (Bitmap image in water) if (image != null) image.Dispose();
            percentFont.Dispose(); statusFont.Dispose(); footerFont.Dispose();
        }
        base.Dispose(disposing);
    }

    [StructLayout(LayoutKind.Sequential)] struct Margins { public int Left, Right, Top, Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct Accent { public int State, Flags; public uint Tint; public int Animation; }
    [StructLayout(LayoutKind.Sequential)] struct Composition { public int Attribute; public IntPtr Data; public UIntPtr Size; }
    [StructLayout(LayoutKind.Sequential, Pack = 1)] struct Blend { public byte Operation, Flags, Alpha, Format; }

    static int ApplyGlass(IntPtr hwnd)
    {
        try {
            int dark = 1, corners = 2;
            DwmSetWindowAttribute(hwnd, 20, ref dark, 4);
            DwmSetWindowAttribute(hwnd, 33, ref corners, 4);
            Margins margins = new Margins { Left = -1, Right = -1, Top = -1, Bottom = -1 };
            DwmExtendFrameIntoClientArea(hwnd, ref margins);
            Accent accent = new Accent { State = 4, Tint = 0x40251A10 };
            IntPtr buffer = Marshal.AllocHGlobal(Marshal.SizeOf(accent));
            try {
                Marshal.StructureToPtr(accent, buffer, false);
                Composition data = new Composition { Attribute = 19, Data = buffer, Size = new UIntPtr((uint)Marshal.SizeOf(accent)) };
                if (SetWindowCompositionAttribute(hwnd, ref data)) return 1;
            } finally { Marshal.FreeHGlobal(buffer); }
            int backdrop = 3;
            if (DwmSetWindowAttribute(hwnd, 38, ref backdrop, 4) == 0) return 2;
        }
        catch (EntryPointNotFoundException) { }
        catch (DllNotFoundException) { }
        return 0;
    }

    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool ReleaseCapture();
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool SetProp(IntPtr hwnd, string name, IntPtr value);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
    [DllImport("gdi32.dll")] static extern IntPtr CreateCompatibleDC(IntPtr dc);
    [DllImport("gdi32.dll")] static extern bool DeleteDC(IntPtr dc);
    [DllImport("gdi32.dll")] static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr obj);
    [DllImport("user32.dll")] static extern bool UpdateLayeredWindow(IntPtr hwnd, IntPtr screen, ref Point target, ref Size size, IntPtr source, ref Point origin, uint key, ref Blend blend, uint flags);
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int key, ref int value, int size);
    [DllImport("dwmapi.dll")] static extern int DwmExtendFrameIntoClientArea(IntPtr hwnd, ref Margins margins);
    [DllImport("user32.dll")] static extern bool SetWindowCompositionAttribute(IntPtr hwnd, ref Composition data);
}
