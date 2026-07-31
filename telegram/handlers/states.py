from aiogram.fsm.state import State, StatesGroup


class TimezoneStates(StatesGroup):
    waiting_tz = State()


class LanguageStates(StatesGroup):
    waiting_lang = State()


class DestinationsStates(StatesGroup):
    waiting_username = State()
    waiting_forward = State()


class ScheduleStates(StatesGroup):
    choosing_destination = State()
    entering_datetime = State()
    selecting_time = State()
    collecting_post = State()
    confirming = State()


class RepeatStates(StatesGroup):
    choosing_interval = State()
    entering_datetime = State()
    selecting_time = State()
    choosing_destination = State()
    collecting_post = State()
    confirming = State()


class DraftStates(StatesGroup):
    choosing_destination = State()
    collecting_post = State()
    choosing_scope = State()
    editing_post = State()
    entering_datetime = State()
    selecting_time = State()
    confirming = State()


class BroadcastStates(StatesGroup):
    choosing_destinations = State()
    entering_datetime = State()
    selecting_time = State()
    collecting_post = State()
    confirming = State()


class AdminBroadcastStates(StatesGroup):
    collecting = State()
    confirming = State()


class EditStates(StatesGroup):
    choosing_field = State()
    entering_text = State()
    entering_datetime = State()
    selecting_time = State()
    collecting_media = State()
