import nuke
import os

def find_upstream_node(start_node, match_class=None, match_name_substr=None, pipe_index=0):
    """ Recursive helper to find specific upstream nodes. """
    current_node = start_node
    for _ in range(100):
        if current_node is None: return None
        if match_class and current_node.Class() == match_class: return current_node
        if match_name_substr and match_name_substr in current_node.name(): return current_node
        try:
            if current_node.Class() == "Merge2":
                current_node = current_node.input(pipe_index)
            else:
                current_node = current_node.input(0)
        except: return None
    return None

def create_breakdown_final():
    # 1. Setup & Selection
    try:
        sel = nuke.selectedNode()
    except ValueError:
        nuke.message("Error: Please select the Final Merge or Main Write node.")
        return

    # Identify Final Merge
    final_merge = sel if sel.Class() == "Merge2" else find_upstream_node(sel, match_class="Merge2", pipe_index=0)
    if not final_merge:
        nuke.message("Error: Could not identify a 'Final Merge'.\nPlease select the main Merge combining FG and BG.")
        return

    # 2. Identify Components
    print(f"Analyzing Graph from: {final_merge.name()}")

    # Component 1: Final Comp (Source for Input 0 & 4)
    node_final = final_merge

    # Component 2: BG (B Pipe of Final Merge)
    node_bg = find_upstream_node(final_merge, match_class="Read", pipe_index=0)

    # Component 3: Plate/Scan (A Pipe -> Read)
    node_plate = find_upstream_node(final_merge, match_class="Read", pipe_index=1)

    # Component 4: Key/Premult (A Pipe -> Premult)
    node_key = find_upstream_node(final_merge, match_class="Premult", pipe_index=1)
    if not node_key: # Fallback to Roto or just use Plate
        node_key = find_upstream_node(final_merge, match_class="Roto", pipe_index=1)

    # Validate
    if not all([node_final, node_bg, node_plate]):
        nuke.message(f"Warning: Could not find all components.\nPlate: {node_plate.name() if node_plate else 'Missing'}\nBG: {node_bg.name() if node_bg else 'Missing'}")
        return

    # 3. Calculate Timing (Middle of Shot)
    first_frame = int(nuke.root()['first_frame'].value())
    last_frame = int(nuke.root()['last_frame'].value())
    middle_frame = int((first_frame + last_frame) / 2)

    # Breakdown Params
    hold_frames = 10
    wipe_duration = 15

    # Calculate Total Length
    # 4 transitions (Final->Scan, Scan->Key, Key->BG, BG->Final)
    # The breakdown adds extra duration.
    # Original Duration: last_frame - first_frame + 1
    # We pause at middle_frame for 'total_bd_len'.
    # So New End Frame = last_frame + total_bd_len
    total_bd_len = (wipe_duration + hold_frames) * 4
    end_frame_needed = last_frame + total_bd_len

    # 4. Create Breakdown Setup
    nuke.root().begin()

    # Create the Gizmo
    bd_maker = nuke.createNode("Breakdown_Maker")
    bd_maker.setName("Auto_Breakdown_Sequence")
    bd_maker.setXYpos(final_merge.xpos() + 200, final_merge.ypos())

    inputs_map = [] # Store nodes to autoplace later

    # Helper to create FrameHold
    def create_hold(input_node, hold_at, label):
        # Fix: using 'first_frame' knob name instead of 'firstFrame' which is Tcl name
        hold = nuke.nodes.FrameHold(first_frame=hold_at, name=f"Hold_{label}")
        hold.setInput(0, input_node)
        hold.setXYpos(bd_maker.xpos() + (len(inputs_map)*100), bd_maker.ypos() - 150)
        return hold

    # --- CONNECT INPUTS ---

    # Input 0: Final Comp (Live playback UNTIL middle frame, then PAUSE)
    # We create a Switch that goes from Live (Input 0) to Frozen (Input 1) at middle_frame.
    hold_final = create_hold(node_final, middle_frame, "Final_Base")
    inputs_map.append(hold_final)

    switch_final = nuke.nodes.Switch(name="Smart_Base_Switch")
    switch_final.setInput(0, node_final) # Live
    switch_final.setInput(1, hold_final) # Frozen
    switch_final.setXYpos(bd_maker.xpos() - 100, bd_maker.ypos() - 150)

    # Switch at middle_frame: Before = 0, At/After = 1
    switch_final['which'].setExpression(f"frame < {middle_frame} ? 0 : 1")

    bd_maker.setInput(0, switch_final)

    # Input 1: Scan (FROZEN at middle frame)
    if node_plate:
        h_plate = create_hold(node_plate, middle_frame, "Plate")
        bd_maker.setInput(1, h_plate)
        inputs_map.append(h_plate)

    # Input 2: Key (FROZEN at middle frame)
    if node_key:
        h_key = create_hold(node_key, middle_frame, "Key")
        bd_maker.setInput(2, h_key)
        inputs_map.append(h_key)

    # Input 3: BG (FROZEN at middle frame)
    if node_bg:
        h_bg = create_hold(node_bg, middle_frame, "BG")
        bd_maker.setInput(3, h_bg)
        inputs_map.append(h_bg)

    # Input 4: Final Comp (Resume playback after breakdown)
    # We use TimeOffset to shift the live footage so it continues from middle_frame
    # after the breakdown duration.
    resume_offset = nuke.nodes.TimeOffset(time_offset=total_bd_len, name="Resume_Offset")
    resume_offset.setInput(0, node_final)
    resume_offset.setXYpos(bd_maker.xpos() + 500, bd_maker.ypos() - 150)
    inputs_map.append(resume_offset)

    bd_maker.setInput(4, resume_offset)

    # 5. Configure Gizmo Knobs (Logic Fixes)
    try:
        k = bd_maker

        # Enable "Play Output First" logic
        if 'playbeforebreakdown' in k.knobs():
            k['playbeforebreakdown'].setValue(1)

        # Start breakdown at the middle of the shot
        if 'start' in k.knobs():
            k['start'].setValue(middle_frame)

        # Set Speed (Wipe Duration)
        if 'Duration' in k.knobs():
            k['Duration'].setValue(wipe_duration)

        # Enable Hold logic
        if 'hold' in k.knobs():
            k['hold'].setValue(1)
        if 'holdingframe' in k.knobs():
            k['holdingframe'].setValue(hold_frames)

        # Disable loop so it resumes/stops at end
        if 'loop' in k.knobs():
            k['loop'].setValue(0)

        # Force update input count (Crucial for "playing just 1 input" bug)
        if 'amountofinputs' in k.knobs():
            k['amountofinputs'].setValue(5)

    except Exception as e:
        print(f"Error setting knobs: {e}")

    # 6. Polish / Cleanup
    for n in inputs_map:
        nuke.autoplace(n)
    nuke.autoplace(switch_final)
    nuke.autoplace(bd_maker)

    # Add backdrop
    bd = nuke.nodes.BackdropNode(xpos=bd_maker.xpos()-50, ypos=bd_maker.ypos()-200, bdwidth=800, bdheight=400, label="Breakdown Gen", note_font_size=20)

    # 7. Write Node & Render Prompt
    if nuke.ask("Ready for render?"):
        # Update Project Frame Range
        nuke.root()['last_frame'].setValue(end_frame_needed)

        # Create Write Node
        write_node = nuke.createNode("Write", "name Write_Breakdown")
        write_node.setXYpos(bd_maker.xpos(), bd_maker.ypos() + 150)
        write_node.setInput(0, bd_maker)

        # Determine Path
        base_path = "Y:/PROJECT/SPDP/production/Breakdowns"

        # Ensure directory exists if possible
        if not os.path.exists(base_path):
            try:
                os.makedirs(base_path)
            except:
                pass

        script_path = nuke.root().name()
        if script_path == "Root":
            script_name = "Untitled"
        else:
            script_name = os.path.basename(script_path)
            script_name = os.path.splitext(script_name)[0]

        file_path = os.path.join(base_path, f"{script_name}_breakdown.mov")
        # Nuke expects forward slashes often even on Windows
        file_path = file_path.replace("\\", "/")

        write_node['file'].setValue(file_path)
        write_node['file_type'].setValue("mov")

        nuke.message(f"Breakdown Created & Write Node Added!\n\nOutput: {file_path}\nFrame Range Extended to: {end_frame_needed}")
    else:
        nuke.message(f"Breakdown Created!\n\nStart Frame: {middle_frame}\nEnd Frame Needed: {end_frame_needed}\n\n*Important*: Ensure your Project Settings 'frame range' covers up to frame {end_frame_needed} to see the full result.")

# Run
create_breakdown_final()
