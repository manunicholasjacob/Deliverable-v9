import curses
import sbr
import device_control
from datetime import datetime
import gpu_burn_script
import time
import run_629_diag
import itertools
import threading

def execute_shell_command(command):
    try:
        result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            return result.stdout.decode("utf-8").strip()
        else:
            return f"Error: {result.stderr.decode('utf-8').strip()}"
    except Exception as e:
        return f"Error: {str(e)}"

def read_class_code(bdf):
    try:
        class_code = execute_shell_command(f"setpci -s {bdf} CLASS")
        return class_code.strip()
    except Exception as e:
        return None

def read_header(bdf):
    try:
        header_type = execute_shell_command(f"setpci -s {bdf} HEADER_TYPE")
        return header_type.strip()
    except Exception as e:
        return None

def read_secondary_bus_number(bdf):
    try:
        secondary_bus_number = execute_shell_command(f"setpci -s {bdf} SECONDARY_BUS")
        return secondary_bus_number.strip()
    except Exception as e:
        return None

def identify_gpus():
    command_output = execute_shell_command("lspci | cut -d ' ' -f 1")
    bdf_list = [num for num in command_output.split('\n') if num]

    gpus = []
    for bdf in bdf_list:
        class_code = read_class_code(bdf)
        header_type = read_header(bdf)
        if class_code and class_code[:2] == '03' and header_type[-2:] == '00':
            gpus.append(bdf)
    return gpus

def trace_to_root_port(bdf):
    current_bus = bdf.split(":")[0]
    while True:
        upstream_connection = None
        all_bdfs = execute_shell_command("lspci | cut -d ' ' -f 1").split('\n')
        header_bdfs = [b for b in all_bdfs if read_header(b).strip()[-2:] == "01"]
        for header_bdf in header_bdfs:
            if read_secondary_bus_number(header_bdf) == current_bus:
                upstream_connection = header_bdf
                break
        if not upstream_connection:
            return bdf  # Return the current BDF if no upstream connection is found
        current_bus = upstream_connection.split(":")[0]
        bdf = upstream_connection

def identify_gpus_and_trace_root_ports():
    gpus = identify_gpus()
    root_ports = []
    for gpu in gpus:
        root_port = trace_to_root_port(gpu)
        root_ports.append(root_port)
    return gpus, root_ports

def main(stdscr):
    curses.echo()

    # Colors and border setup
    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
    stdscr.bkgd(curses.color_pair(1))

    def display_box(window, y, x, height, width, title=""):
        window.attron(curses.color_pair(2))
        window.border(0)
        window.addstr(0, 2, f' {title} ')
        window.attroff(curses.color_pair(2))
        window.refresh()

    def scroll_output(window, window_offset_y, window_offset_x, window_height, window_width, pad_pos):
        scroll_pad = pad_pos
        cmd = ''
        while True:
            cmd = window.getch()
            if cmd == ord('q'): break
            if cmd == curses.KEY_DOWN:
                if scroll_pad < pad_pos: scroll_pad += 1
                window.refresh(scroll_pad, 0, window_offset_y, window_offset_x, min(curses.LINES-1, window_offset_y + window_height - 3), min(curses.COLS-1, window_offset_x + window_width - 5))
            elif cmd == curses.KEY_UP:
                if scroll_pad > 0: scroll_pad -= 1
                window.refresh(scroll_pad, 0, window_offset_y, window_offset_x, min(curses.LINES-1, window_offset_y + window_height - 3), min(curses.COLS-1, window_offset_x + window_width - 5))

    slot_numbers = sbr.get_slot_numbers()
    gpu_info_list = gpu_burn_script.gpu_traverse_up()

    height = max(len(slot_numbers) + 4, len(gpu_info_list) + 4, 10)

    slot_window_width = 30
    slot_window = curses.newwin(height, slot_window_width, 1, 1)
    display_box(slot_window, 1, 1, height, slot_window_width, "Available Slot Numbers")
    slot_window.addstr(1, 2, 'Slot Number\tBDF'.expandtabs(5))
    for i, slot in enumerate(slot_numbers):
        slot = slot.split(" : ")
        slot_window.addstr(i + 2, 2, '{:<14s} {:<10s}'.format(slot[0], slot[1]))
    slot_window.refresh()

    gpu_window_height = height  
    gpu_window_width = 75
    gpu_window = curses.newwin(gpu_window_height, gpu_window_width, 1, slot_window_width+3)
    display_box(gpu_window, 1, 41, gpu_window_height, gpu_window_width, "GPU Info")
    for i, gpu_info in enumerate(gpu_info_list):
        gpu_print = f"GPU {i}\t|\tBDF: {gpu_info[0]}\t|\tSlot: {gpu_info[1]}\t|\tRoot Port: {gpu_info[2]}\t|\tPSB {gpu_info[3]}"
        gpu_window.addstr(i+2, 2, gpu_print.expandtabs(3))
    gpu_window.refresh()

    output_window_height = 20
    output_window_width = 55
    output_window = curses.newpad(10000, 55)
    output_window_border = curses.newwin(output_window_height, output_window_width, height + 2, 50+3)
    display_box(output_window_border, 10, 41, height, slot_window_width+3, "Output")
    pad_pos = 0

    input_window_height = 20
    input_window_width = 50
    input_window = curses.newwin(input_window_height-4, input_window_width-4, height + 4, 3)
    input_window_border = curses.newwin(input_window_height, input_window_width, height + 2, 1)
    display_box(input_window_border, height + 2, 1, input_window_height, input_window_width, "Command Line")

    input_window.addstr(0, 0, "Choose operation (s: SBR, g: GPU Burn, d: 629 Diag, sg: SBR GPUs only | comma separated): ")
    operations_input = input_window.getstr().decode().lower()
    operations = [operation.strip() for operation in operations_input.split(',')]
    all_valid = True
    for operation in operations:
        if operation not in ['s','g','d', 'sg']: all_valid = False 
    while not all_valid:
        input_window.clear()
        input_window.addstr(0, 0, "Invalid Input - (s: SBR, g: GPU Burn, d: 629 Diag, sg: SBR GPUs only | comma separated): ")
        operations_input = input_window.getstr().decode().lower()
        operations = [operation.strip() for operation in operations_input.split(',')]
        all_valid = True
        for operation in operations:
            if operation not in ['s','g','d', 'sg']: all_valid = False

    input_window.addstr(3, 0, "Enter your password (sudo access): ")
    user_password = input_window.getstr().decode()

    if 'sg' in operations:
        gpus, root_ports = identify_gpus_and_trace_root_ports()
        slotlist = root_ports
        input_window.addstr(6, 0, "Identified GPUs and their Root Ports.")
        for gpu, root_port in zip(gpus, root_ports):
            input_window.addstr(7, 0, f"GPU: {gpu}, Root Port: {root_port}")
    elif 's' in operations:
        input_window.addstr(0, 0, "SBR Settings")
        input_window.addstr(2, 0, "Number of Loops: ")
        inputnum_loops = int(input_window.getstr().decode())
        input_window.addstr(4, 0, "Do you want to kill on error? (y/n): ")
        kill = input_window.getstr().decode()
        input_window.addstr(6, 0, "Choose slot numbers to test (comma separated): ")
        slot_input = input_window.getstr().decode()
        slotlist = list(map(int, slot_input.split(',')))
    
    # Implement GPU Burn and 629 Diag options here...
    
    # For SBR or SBR GPUs only operations
    if 's' in operations or 'sg' in operations:
        input_window.clear()
        sbr.run_test(input_window, user_password, inputnum_loops, kill, slotlist)

    input_window.refresh()
    input_window.getch()

curses.wrapper(main)
