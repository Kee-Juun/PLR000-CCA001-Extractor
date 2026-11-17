"""Author the fluid startup card and embed it in PyInstaller's early splash."""
from pathlib import Path

from PyInstaller.building.splash import Splash


SPLASH_WIDTH = 560
SPLASH_HEIGHT = 312
STATUS_POSITION = (220, 154)
WATER_BOX = (12, 164, 548, 300)
WATER_FRAMES = 48


def create_startup_artwork(output: Path, root: Path, *, frosted: bool = False) -> Path:
    """Render the glass backdrop and typography using the application's palette."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    scale = 3
    width, height = SPLASH_WIDTH, SPLASH_HEIGHT
    image = Image.new('RGBA', (width * scale, height * scale))
    draw = ImageDraw.Draw(image)
    for y in range(height * scale):
        t = y / (height * scale)
        draw.line((0, y, width * scale, y), fill=(int(9 + 3*t), int(18 + 6*t), int(33 + 11*t), int(124+30*t) if frosted else 255))
    glow = Image.new('RGBA', image.size)
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-120*scale, 150*scale, 350*scale, 390*scale), fill=(7, 100, 135, 85))
    glow_draw.ellipse((320*scale, -150*scale, 670*scale, 145*scale), fill=(91, 66, 147, 42))
    image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(50*scale)))
    draw = ImageDraw.Draw(image)

    def font(size, bold=False):
        filename = 'segoeuib.ttf' if bold else 'segoeui.ttf'
        path = Path('C:/Windows/Fonts') / filename
        return ImageFont.truetype(str(path), size*scale) if path.is_file() else ImageFont.load_default(size=size*scale)

    def label(position, text, size, fill, bold=False):
        draw.text(tuple(value*scale for value in position), text, font=font(size, bold), fill=fill)

    label((36, 31), 'STARTING UP', 10, '#67e8f9', True)
    label((36, 145), 'Estimated progress', 11, '#91a9bf')
    draw.line((194*scale, 38*scale, 194*scale, 159*scale), fill='#26384d', width=scale)
    label((220, 34), 'LEXIS CITE EXTRACTOR', 10, '#67e8f9', True)
    label((217, 58), 'PLR000-CCA001', 27, '#f1f7ff', True)
    label((220, 101), 'Loading your workspace', 12, '#b6c7dd')
    draw.rounded_rectangle((0, 0, width*scale-1, height*scale-1), radius=23*scale,
                           outline='#2a4058', width=scale)
    mask = Image.new('L', image.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width*scale-1, height*scale-1), radius=23*scale, fill=255)
    if frosted:
        from PIL import ImageChops
        image.putalpha(ImageChops.multiply(image.getchannel('A'), mask))
    else:
        image.putalpha(mask)
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    if not frosted:
        image.putalpha(image.getchannel('A').point(lambda alpha: 255 if alpha >= 128 else 0))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output


def create_water_frames(directory: Path) -> list[Path]:
    """Render a seamless loop of translucent waves, caustics and rising bubbles."""
    import math
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

    directory.mkdir(parents=True, exist_ok=True)
    scale = 2
    width, height = WATER_BOX[2]-WATER_BOX[0], WATER_BOX[3]-WATER_BOX[1]
    size = (width*scale, height*scale)
    fade_x = Image.new('L', (size[0], 1))
    fade_x.putdata([int(255*min(1, x/(18*scale), (size[0]-1-x)/(18*scale))) for x in range(size[0])])
    fade_y = Image.new('L', (1, size[1]))
    fade_y.putdata([int(255*min(1, (size[1]-1-y)/(18*scale))) for y in range(size[1])])
    edge = ImageChops.multiply(fade_x.resize(size), fade_y.resize(size))
    gradients = []
    for top, bottom in [('#164f77', '#091b36'), ('#167fa3', '#0b2945'), ('#29b1c5', '#0b253c')]:
        gradients.append(ImageOps.colorize(Image.linear_gradient('L'), top, bottom).resize(size).convert('RGBA'))
    paths = []
    for frame in range(WATER_FRAMES):
        t = frame/WATER_FRAMES*math.tau
        image = Image.new('RGBA', size)
        for layer, gradient in enumerate(gradients):
            crest = []
            for x in range(0, width+1, 2):
                u = x/width
                y = (36+layer*20 + math.sin(u*math.tau+t*(1 if layer != 1 else -1)+layer*.7)*14
                     + math.sin(u*math.tau*2-t*2+layer)*5 + math.cos(u*math.pi)*7)
                crest.append((x*scale, y*scale))
            mask = Image.new('L', size)
            ImageDraw.Draw(mask).polygon([*crest, (size[0], size[1]), (0, size[1])], fill=220 if layer < 2 else 245)
            water = gradient.copy()
            water.putalpha(mask)
            image = Image.alpha_composite(image, water)
            light = Image.new('RGBA', size)
            ImageDraw.Draw(light).line(crest, fill=(56, 209, 237, 100), width=5*scale)
            image = Image.alpha_composite(image, light.filter(ImageFilter.GaussianBlur(4*scale)))
            ImageDraw.Draw(image).line(crest, fill=(145, 239, 249, 90+layer*35), width=scale)
        detail = Image.new('RGBA', size)
        draw = ImageDraw.Draw(detail)
        for line in range(8):
            points = []
            for x in range(0, width+1, 3):
                y = 92+line*5+math.sin(x/width*math.tau+t+line*.32)*9+math.sin(x/width*math.tau*2-t)*4
                points.append((x*scale, y*scale))
            draw.line(points, fill=(94, 224, 239, 12+line%3*6), width=scale)
        for bubble in range(9):
            phase = (frame/WATER_FRAMES+bubble/9) % 1
            x = 35+bubble*56+math.sin(t+bubble)*6
            y = height-phase*75
            radius = 1.2+(bubble%3)*.6
            alpha = int(60*math.sin(phase*math.pi))
            draw.ellipse(((x-radius)*scale, (y-radius)*scale, (x+radius)*scale, (y+radius)*scale),
                         outline=(165, 239, 246, alpha), width=scale)
        image = Image.alpha_composite(image, detail)
        image.putalpha(ImageChops.multiply(image.getchannel('A'), edge))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        path = directory / f'water_{frame:03d}.png'
        image.save(path, optimize=True)
        paths.append(path)
    return paths


NATIVE_WINDOW_SCRIPT = r'''
set startup_native_active 0
set startup_native_pending 0
set startup_native_channel {}
set startup_minimized 0
set startup_glass_mode 0
set startup_message "Preparing application..."
set startup_token [format %x%x%x [pid] [clock microseconds] [expr {int(rand()*0x7fffffff)}]]
proc startup_control_accept {channel address port} {
    fconfigure $channel -blocking 0 -buffering line -encoding utf-8 -translation lf
    fileevent $channel readable [list startup_control_read $channel]
}
proc startup_control_read {channel} {
    global startup_token startup_native_channel startup_native_active startup_native_pending startup_minimized startup_glass_mode startup_progress
    if {[eof $channel]} {
        catch {close $channel}
        if {$channel eq $startup_native_channel} {
            set startup_native_active 0
            set startup_native_channel {}
            show_startup_fallback
        }
        return
    }
    if {[gets $channel line] < 0} {return}
    set line [string trimright $line "\r"]
    set parts [split $line "\t"]
    set command [lindex $parts 0]
    if {$command eq "HELLO" && [llength $parts] == 3 && [lindex $parts 1] eq $startup_token && $startup_native_channel eq {}} {
        set startup_native_channel $channel
        set startup_native_active 1
        set startup_native_pending 0
        set startup_glass_mode [lindex $parts 2]
        wm withdraw .
        send_native_progress
    } elseif {$command eq "STATE" && $channel eq $startup_native_channel} {
        set startup_minimized [expr {[lindex $parts 1] eq "minimized"}]
    } elseif {$command eq "QUERY" && [lindex $parts 1] eq $startup_token} {
        catch {puts $channel "STATE\t$startup_minimized\t$startup_glass_mode\t$startup_progress"; flush $channel}
        close $channel
    } else {
        catch {close $channel}
    }
}
proc send_native_progress {} {
    global startup_native_channel startup_progress startup_message
    if {$startup_native_channel ne {}} {
        set encoded [binary encode base64 -maxlen 0 [encoding convertto utf-8 $startup_message]]
        catch {puts $startup_native_channel "PROGRESS\t$startup_progress\t$encoded"}
    }
}
proc startup_fallback_state {} {
    global startup_native_active startup_minimized
    if {!$startup_native_active} {set startup_minimized [expr {[wm state .] eq "iconic"}]}
}
proc show_startup_fallback {} {
    global startup_native_active startup_native_pending
    if {$startup_native_active} {return}
    set startup_native_pending 0
    wm overrideredirect . 0
    wm attributes . -topmost 0
    wm resizable . 0 0
    wm protocol . WM_DELETE_WINDOW {wm iconify .}
    bind . <Unmap> {after idle startup_fallback_state}
    bind . <Map> {after idle startup_fallback_state}
    wm deiconify .
}
proc start_native_window {} {
    global startup_native_executable startup_native_pending startup_native_pid startup_token env
    set listener [socket -server startup_control_accept -myaddr 127.0.0.1 0]
    set env(PLR_STARTUP_CONTROL_PORT) [lindex [fconfigure $listener -sockname] 2]
    set env(PLR_STARTUP_CONTROL_TOKEN) $startup_token
    if {![info exists startup_native_executable]} {
        set startup_native_executable [file join [file dirname [info library]] plr_startup.exe]
    }
    if {[file isfile $startup_native_executable]} {
        set startup_native_pending 1
        if {[catch {exec $startup_native_executable $env(PLR_STARTUP_CONTROL_PORT) $startup_token [pid] &} startup_native_pid]} {
            show_startup_fallback
        } else {
            after 4000 show_startup_fallback
        }
    } else {
        show_startup_fallback
    }
}
'''


ACTIVITY_SCRIPT = r'''
wm title . "PLR000-CCA001 Extractor - Starting"
font configure myFont -family {Segoe UI} -size -13
.root.canvas itemconfigure vartext -text "Preparing application..." -width 300 -anchor sw
font create startup_percent_font -family {Segoe UI Light} -size -62
.root.canvas create text 32 99 -anchor w -font startup_percent_font -fill #eefbff -text "0%" -tags startup_percentage
set startup_progress 0
set startup_completed_bytes 0
set startup_previous_file {}
set startup_seen [dict create]

proc set_startup_progress {value} {
    global startup_progress
    set startup_progress [expr {max($startup_progress, min(100, int($value)))}]
    .root.canvas itemconfigure startup_percentage -text "${startup_progress}%"
    send_native_progress
}

# Credit a file only when the next extraction message confirms it completed.
# Python reports the later real startup milestones. Never show dependency paths.
proc canvas_text_update {canvas tag _var - -} {
    upvar $_var value
    global startup_file_sizes startup_total_bytes startup_completed_bytes startup_previous_file startup_seen startup_message
    if {[string first "@stage:" $value] == 0} {
        set message [string range $value 7 end]
        if {[regexp {^([0-9]+)\|(.*)$} $message - progress message]} {
            set_startup_progress $progress
        }
        set startup_message $message
        $canvas itemconfigure $tag -text $message
        send_native_progress
    } else {
        if {$startup_previous_file ne {} && [dict exists $startup_file_sizes $startup_previous_file] && ![dict exists $startup_seen $startup_previous_file]} {
            incr startup_completed_bytes [dict get $startup_file_sizes $startup_previous_file]
            dict set startup_seen $startup_previous_file 1
        }
        set startup_previous_file [string map {\\ /} $value]
        set_startup_progress [expr {min(55, 55.0*$startup_completed_bytes/max(1,$startup_total_bytes))}]
    }
}

# This timer lives in the bootloader Tcl thread, independently of Python/Qt.
set startup_loaded 1
set startup_frame 0
set startup_frame_count [llength $startup_water_data]
image create photo startup_water_0 -data [lindex $startup_water_data 0]
.root.canvas create image 12 164 -anchor nw -image startup_water_0 -tags startup_water
font create startup_footer_font -family {Segoe UI} -size -10
.root.canvas create text 36 285 -anchor w -font startup_footer_font -fill #8eafbf -text "This window closes when your workspace is ready."
.root.canvas raise vartext
.root.canvas raise startup_percentage

proc load_startup_water {} {
    global startup_loaded startup_frame_count startup_water_data startup_native_active startup_native_pending
    if {$startup_native_active || $startup_native_pending} {after 100 load_startup_water; return}
    if {$startup_loaded < $startup_frame_count} {
        image create photo startup_water_$startup_loaded -data [lindex $startup_water_data $startup_loaded]
        incr startup_loaded
        after 1 load_startup_water
    } else {
        unset startup_water_data
    }
}
proc startup_flow {} {
    global startup_frame startup_loaded startup_frame_count startup_native_active
    if {!$startup_native_active && $startup_loaded == $startup_frame_count} {
        set startup_frame [expr {($startup_frame+1)%$startup_frame_count}]
        .root.canvas itemconfigure startup_water -image startup_water_$startup_frame
    }
    after 40 startup_flow
}
after 1 load_startup_water
startup_flow
update idletasks
start_native_window
'''


class ExtractorSplash(Splash):
    """Preserve PyInstaller's lifecycle while animating the early native window."""

    def __init__(self, *args, startup_files=None, water_frames=None, native_executable=None, **kwargs):
        self.startup_files = startup_files or {}
        self.water_frames = water_frames or []
        self.native_executable = native_executable
        super().__init__(*args, **kwargs)

    def assemble(self):
        if self.native_executable:
            self.binaries.append(('plr_startup.exe', str(self.native_executable), 'DATA'))
            self.splash_requirements.add('plr_startup.exe')
        super().assemble()

    def generate_script(self):
        import base64
        from tkinter import _join

        if not self.water_frames:
            raise ValueError('The startup water animation has no frames')
        files = self.startup_files
        metadata = ('set startup_file_sizes [dict create ' + _join([str(item) for pair in files.items() for item in pair]) + ']\n'
                    + f'set startup_total_bytes {sum(files.values())}\n')
        frames = 'set startup_water_data [list ' + ' '.join(
            '{' + base64.b64encode(path.read_bytes()).decode('ascii') + '}' for path in self.water_frames) + ']\n'
        script = 'wm withdraw .\n' + super().generate_script() + '\n' + metadata + frames + NATIVE_WINDOW_SCRIPT + ACTIVITY_SCRIPT
        Path(self.script_name).write_text(script, encoding='utf-8')
        return script


def build_splash(root: Path, work_directory: Path, analysis):
    image = create_startup_artwork(work_directory / 'startup_splash.png', root)
    frames = create_water_frames(work_directory / 'startup_water')
    from tools.native_startup_build import build_native_startup
    glass = create_startup_artwork(work_directory / 'startup_glass.png', root, frosted=True)
    native = build_native_startup(root, work_directory, glass, frames)
    files = {}
    for destination, source, _kind in [*analysis.binaries, *analysis.datas]:
        path = Path(source)
        if path.is_file():
            files[str(destination).replace('\\', '/')] = path.stat().st_size
    return ExtractorSplash(
        str(image), binaries=analysis.binaries, datas=analysis.datas,
        startup_files=files, water_frames=frames, native_executable=native,
        text_pos=STATUS_POSITION, text_size=11, text_font='{Segoe UI}',
        text_color='#d4e6f4', text_default='@stage:0|Preparing application...',
        always_on_top=True, minify_script=False,
    )


if __name__ == '__main__':
    output = Path(__file__).resolve().parents[1] / 'outputs' / 'startup-preview'
    print(create_startup_artwork(output / 'startup_splash.png', output.parents[1]))
    print(len(create_water_frames(output / 'startup_water')), 'water frames')
