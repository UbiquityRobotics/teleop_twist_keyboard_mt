# Copyright 2011 Brown University Robotics.
# Copyright 2017 Open Source Robotics Foundation, Inc.
# All rights reserved.
#
# Software License Agreement (BSD License 2.0)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of the Willow Garage nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import sys
import threading

import geometry_msgs.msg
import rcl_interfaces.msg
import rclpy
from std_srvs.srv import SetBool
from std_msgs.msg import Int64

if sys.platform == 'win32':
    import msvcrt
else:
    import termios
    import tty


msg = """
This node takes keypresses from the keyboard and publishes them
as Twist/TwistStamped messages. It works best with a US keyboard layout.
---------------------------
Moving around:
   u    i    o
   j    k    l
   m    ,    .

For Holonomic mode (strafing), hold down the shift key:
---------------------------
   U    I    O
   J    K    L
   M    <    >

t : up (+z)
b : down (-z)

Microtractor tool control:
---------------------------
h: tool up
n: tool down
f: tool start
v: tool stop
a: tool less deep
y: tool deeper

anything else : stop

q/z : increase/decrease max speeds by 10%
w/x : increase/decrease only linear speed by 10%
e/c : increase/decrease only angular speed by 10%

CTRL-C to quit
"""

moveBindings = {
    'i': (1, 0, 0, 0),
    'o': (1, 0, 0, -1),
    'j': (0, 0, 0, 1),
    'l': (0, 0, 0, -1),
    'u': (1, 0, 0, 1),
    ',': (-1, 0, 0, 0),
    '.': (-1, 0, 0, 1),
    'm': (-1, 0, 0, -1),
    'O': (1, -1, 0, 0),
    'I': (1, 0, 0, 0),
    'J': (0, 1, 0, 0),
    'L': (0, -1, 0, 0),
    'U': (1, 1, 0, 0),
    '<': (-1, 0, 0, 0),
    '>': (-1, -1, 0, 0),
    'M': (-1, 1, 0, 0),
    't': (0, 0, 1, 0),
    'b': (0, 0, -1, 0),
}

speedBindings = {
    'q': (1.1, 1.1),
    'z': (.9, .9),
    'w': (1.1, 1),
    'x': (.9, 1),
    'e': (1, 1.1),
    'c': (1, .9),
}


def getKey(settings):
    if sys.platform == 'win32':
        # getwch() returns a string on Windows
        key = msvcrt.getwch()
    else:
        tty.setraw(sys.stdin.fileno())
        # sys.stdin.read() returns a string on Linux
        key = sys.stdin.read(1)
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
    return key


def saveTerminalSettings():
    if sys.platform == 'win32':
        return None
    return termios.tcgetattr(sys.stdin)


def restoreTerminalSettings(old_settings):
    if sys.platform == 'win32':
        return
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def vels(speed, turn):
    return 'currently:\tspeed %.2f\tturn %.2f ' % (speed, turn)

def tool_depth_str(depth):
    return 'currently:\ttool depth %d ' % (depth)

tool_timer = None

def start_tool(speed_pub):
    global tool_timer

    speed = -1
    while speed == -1:
        speed = input("Enter speed (positive integer, 40 is default): ")
        if speed == "":
            speed = 40
        else:
            try:
                speed = int(speed)
                if speed <= 0 or speed > 50:
                    speed = -1
                    print("Speed should be greater than 0 and not higher than 50!")
            except ValueError:
                speed = -1
                print("Error while converting provided value to a number!")
    
    timeout = -1
    while timeout == -1:
        timeout = input("Enter timeout in seconds (positive integer, 20 is default): ")
        if timeout == "":
            timeout = 20
        else:
            try:
                timeout = int(timeout)
                if timeout <= 0:
                    timeout = -1
                    print("Timeout should be greater than 0!")
            except ValueError:
                timeout = -1
                print("Error while converting provided value to a number!")
    speed_pub.publish(Int64(data=speed))
    
    def stop_tool():
        global tool_timer
        speed_pub.publish(Int64(data=0))
        tool_timer = None

    tool_timer = threading.Timer(timeout, stop_tool)
    tool_timer.start()
                
def main():
    global tool_timer

    settings = saveTerminalSettings()

    rclpy.init()

    node = rclpy.create_node('teleop_twist_keyboard')

    # parameters
    read_only_descriptor = rcl_interfaces.msg.ParameterDescriptor(read_only=True)
    stamped = node.declare_parameter('stamped', False, read_only_descriptor).value
    frame_id = node.declare_parameter('frame_id', '', read_only_descriptor).value
    speed = node.declare_parameter('speed', 0.5, read_only_descriptor).value
    turn = node.declare_parameter('turn', 1.0, read_only_descriptor).value

    if not stamped and frame_id:
        raise Exception("'frame_id' can only be set when 'stamped' is True")

    if stamped:
        TwistMsg = geometry_msgs.msg.TwistStamped
    else:
        TwistMsg = geometry_msgs.msg.Twist

    pub = node.create_publisher(TwistMsg, 'cmd_vel', 10)

    calibrate_client = node.create_client(SetBool, 'mt_tool/actuator_calibrate')
    if not calibrate_client.wait_for_service(timeout_sec=5.0):
        node.get_logger().warn('Service mt_tool/actuator_calibrate not available yet…')

    # Publishers: /mt_tool/actuator_target and /mt_tool/pto_speed (std_msgs/Int64)
    target_pub = node.create_publisher(Int64, '/mt_tool/actuator_target', 10)
    speed_pub  = node.create_publisher(Int64, '/mt_tool/pto_speed', 10)

    spinner = threading.Thread(target=rclpy.spin, args=(node,))
    spinner.start()

    x = 0.0
    y = 0.0
    z = 0.0
    th = 0.0
    status = 0.0
    tool_depth = 450

    twist_msg = TwistMsg()

    if stamped:
        twist = twist_msg.twist
        twist_msg.header.stamp = node.get_clock().now().to_msg()
        twist_msg.header.frame_id = frame_id
    else:
        twist = twist_msg

    try:
        print(msg)
        print(vels(speed, turn))
        while True:
            key = getKey(settings)
            if key in moveBindings.keys():
                x = moveBindings[key][0]
                y = moveBindings[key][1]
                z = moveBindings[key][2]
                th = moveBindings[key][3]
            elif key in speedBindings.keys():
                speed = speed * speedBindings[key][0]
                turn = turn * speedBindings[key][1]

                print(vels(speed, turn))
                if (status == 14):
                    print(msg)
                status = (status + 1) % 15
            elif key in ['h', 'n', 'f', 'v', 'a', 'y']:
                # Commands for microtractor tool
                if key == 'h':
                    req = SetBool.Request()
                    req.data = True
                    calibrate_client.call_async(req)
                elif key == 'n':
                    target_pub.publish(Int64(data=tool_depth))
                elif key == 'f':
                    start_tool(speed_pub)
                elif key == 'v':
                    # Stop the tool
                    speed_pub.publish(Int64(data=0))
                    if tool_timer is not None:
                        tool_timer.cancel()
                        tool_timer = None
                elif key == 'a':
                    tool_depth -= 10
                    if tool_depth < 0:
                        tool_depth = 0
                    print(tool_depth_str(tool_depth))
                elif key == 'y':
                    tool_depth += 10
                    print(tool_depth_str(tool_depth))
            else:
                x = 0.0
                y = 0.0
                z = 0.0
                th = 0.0
                if (key == '\x03'):
                    break

            if stamped:
                twist_msg.header.stamp = node.get_clock().now().to_msg()

            twist.linear.x = x * speed
            twist.linear.y = y * speed
            twist.linear.z = z * speed
            twist.angular.x = 0.0
            twist.angular.y = 0.0
            twist.angular.z = th * turn
            pub.publish(twist_msg)

    except Exception as e:
        print(e)

    finally:
        if stamped:
            twist_msg.header.stamp = node.get_clock().now().to_msg()

        twist.linear.x = 0.0
        twist.linear.y = 0.0
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = 0.0
        pub.publish(twist_msg)
        rclpy.shutdown()
        spinner.join()

        restoreTerminalSettings(settings)


if __name__ == '__main__':
    main()
