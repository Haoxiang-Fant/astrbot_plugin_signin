# -*- coding: utf-8 -*-
# 功能模块包：各 Mixin 由 main.py 的 SignInPlugin 组合继承。
from .base import CoreMixin

from .farm import FarmMixin
from .pet import PetMixin
from .bank import BankMixin
from .redpacket import RedpacketMixin
from .activities import ActivityMixin
from .loans import LoanMixin
from .roulette import RouletteMixin
from .rank import RankMixin
from .lan import LanMixin
from .perm import PermMixin
from .webui import WebUIMixin

__all__ = ["FarmMixin", "PetMixin", "BankMixin", "RedpacketMixin", "ActivityMixin", "LoanMixin", "RouletteMixin", "RankMixin", "LanMixin", "PermMixin", "WebUIMixin", "CoreMixin"]
