"""Deadline-based interpolation: skipped frames never extend the requested duration."""
import time
import math
from .qt_compat import QtCore

def motion_timing(settings,distance,view_span,camera=False):
    """Distance-dependent eased motion, capped in wall time; late frames are skipped.

    Speed is measured in view widths per second for the camera, and micrometres
    per second for physical preview. FPS sets the requested redraw interval.
    """
    level=settings.camera_speed if camera else settings.animation_speed
    rate=(max(view_span,1)*.8 if camera else 140.)*2**((level-3)/2)
    duration=min(settings.animation_duration_ms,max(40.,1000*distance/rate))
    return duration,max(1,math.ceil(duration/1000*settings.animation_fps))

class Transition(QtCore.QObject):
    def __init__(self,parent):
        super().__init__(parent);self.timer=QtCore.QTimer(self);self.timer.timeout.connect(self.tick);self.complete=None;self.last_duration_ms=None
        self.frame_cost=.012;self.finish_cost=.025
    def start(self,duration_ms,steps,update,complete):
        self.stop();self.started=time.perf_counter();self.duration=duration_ms/1000;self.steps=steps;self.update=update;self.complete=complete;self.last=-1
        self.timer.start(max(8,round(duration_ms/steps)));self.tick()
    def stop(self):
        self.timer.stop();self.complete=None;self.update=None
    def tick(self):
        if not self.timer.isActive() or self.update is None:return
        now=time.perf_counter();t=min(1.,(now-self.started)/self.duration);index=int(t*self.steps)
        # Reserve measured rendering time for the exact endpoint. Otherwise a
        # costly penultimate frame followed by final drawing adds two frames
        # beyond the user's duration cap.
        reserve=min(self.duration*.15,self.finish_cost+max(self.frame_cost,self.duration/self.steps)*1.15)
        if t>=1 or (self.last>=0 and self.duration-(now-self.started)<=reserve):
            done=self.complete;self.stop()
            if done:
                start=time.perf_counter();done();self.finish_cost=time.perf_counter()-start
            self.last_duration_ms=(time.perf_counter()-self.started)*1000
        elif index!=self.last:
            self.last=index;u=index/self.steps;start=time.perf_counter();self.update(u*u*(3-2*u))
            self.frame_cost=time.perf_counter()-start
