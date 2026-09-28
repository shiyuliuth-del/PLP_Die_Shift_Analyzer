"""One to five minor marks per major interval, using interval length and pixels."""
import numpy as np
from matplotlib.ticker import Locator

class DistanceMinorLocator(Locator):
    def __call__(self):
        major=np.asarray(self.axis.get_majorticklocs());lo,hi=sorted(self.axis.get_view_interval())
        if len(major)<2 or hi<=lo:return []
        gap=float(np.median(np.diff(major)));pixels=self.axis.axes.bbox.width if self.axis.axis_name=='x' else self.axis.axes.bbox.height
        interval_pixels=pixels*gap/(hi-lo)
        # At most six subintervals; use a pleasant subdivision for this physical major step.
        mantissa=gap/10**np.floor(np.log10(gap));preferred=5 if abs(mantissa-5)<.1 else (4 if abs(mantissa-2)<.1 else 6)
        subdivisions=min(preferred,max(2,min(6,int(interval_pixels/15))))
        values=np.concatenate([a+gap*np.arange(1,subdivisions)/subdivisions for a in major[:-1]])
        return self.raise_if_exceeds(values[(values>=lo)&(values<=hi)])
    def tick_values(self,vmin,vmax):return self()
