from cv_bridge import CvBridge, CvBridgeError
from message_filters import ApproximateTimeSynchronizer, Subscriber
from sensor_msgs.msg import Image, Range
from qtcopter.msg import controller_msg
import rospy
import smach
import threading
import tf


class MissionState(smach.State):
    def __init__(self, delta_pub, pid_input_pub, debug_pub, outcomes=[], input_keys=[], output_keys=[]):
        smach.State.__init__(self,
                             outcomes=outcomes,
                             input_keys=input_keys,
                             output_keys=output_keys)
        self._delta_pub = delta_pub
        #self._pid_input_pub = pid_input_pub
        self._debug_pub = debug_pub
        self._trigger_event = threading.Event()
        self._bridge = CvBridge()
        self._input_keys = input_keys
        self._output_keys = output_keys
        rospy.logdebug('%s initialized.'.format(self))

    def publish_delta(self, camera_frame, translation, theta):
        rospy.loginfo('Publish delta: x {0}, y {1}, z {2}, theta {3}'.format(translation[0],
           translation[1],
           translation[2],
           theta))
        rotation = tf.transformations.quaternion_from_euler(0, 0, theta)
        self._delta_pub.sendTransform(translation,
                                      rotation,
                                      rospy.Time.now(),
                                      'waypoint',
                                      camera_frame)
        msg = controller_msg()
        msg.x = translation[0]
        msg.y = translation[1]
        msg.z = translation[2]
        msg.t = theta
        #self._pid_input_pub.publish(msg)

    def publish_debug_image(self, callback):
        if self._debug_pub.get_num_connections() <= 0:
            return

        image = callback()
        img_msg = self._bridge.cv2_to_imgmsg(image, encoding='bgr8')
        self._debug_pub.publish(img_msg)

    def on_execute(self):
        '''
        Returns the successor state (one of the "outcomes") or
        None if state should continue to run.
        '''
        return NotImplemented

    def callback(self, range_msg, img_msg, userdata, finishdata):
        if self._finished:
            return

        height = range_msg.range
        try:
            image = self._bridge.imgmsg_to_cv2(img_msg,
                                               desired_encoding='bgr8')
        except CvBridgeError as error:
            rospy.logerror(error)

        output = self.on_execute(userdata, image, height)

        if output is None:
            # Don't terminate this state yet,
            # try again with new sensor input.
            return

        self._finished = True

        finishdata.append(output)

        self._height_sub.sub.unregister()
        self._image_sub.sub.unregister()
        self._trigger_event.set()

    def execute(self, userdata):
        # If prempted before even getting a chance, give up.
        if self.preempt_requested():
            self.service_preempt()
            return 'aborted'

        self._trigger_event.clear()

        self._height_sub = Subscriber('height', Range)
        self._image_sub = Subscriber('image', Image)
        sync = ApproximateTimeSynchronizer([self._height_sub, self._image_sub],
                                           queue_size=1, slop=0.05)

        finishdata = []
        self._finished = False
        sync.registerCallback(self.callback, userdata, finishdata)

        # Wait until a candidate has been found.
        self._trigger_event.wait()
        del sync

        if self.preempt_requested():
            self.service_preempt()
            return 'aborted'

        assert(len(finishdata) == 1)
        return finishdata[0]

    def request_preempt(self):
        smach.State.request_preempt(self)
        self._trigger_event.set()
