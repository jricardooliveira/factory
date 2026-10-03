"""The human's home: what needs you, what is working, the one next thing — then the detail.

Every action writes a record (a job, an answer) and returns; model calls happen in
the detached worker. The screen polls the database every 2 s. What an item SAYS is
`views` (pure text); this module only arranges it and wires the buttons.
"""
from __future__ import annotations

import json
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (Button, DataTable, Footer, Header, Select, SelectionList, Static, Tab,
                             Tabs, TextArea)

from factory.interfaces.board.answer import AnswerPicker
from factory.interfaces.board.interview_screen import PromptScreen
from factory.interfaces.board.views import (
    batch_body, batch_choice, decision_actions, decision_body, decision_head, decision_row,
    event_body, event_text, job_body, job_row, local_time, next_step, project_actions, story_body,
)
from factory.runs.batches import abandon_batch, launch_batch, propose_batch, queue_integration, queue_release
from factory.runs.dashboard import dashboard, pause_project, recover_job, stop_job
from factory.runs.refinement import (answer, request_changes, retry_job, save_draft,
                                      start_backlog, start_refinement)
from factory.runs.worker import start_worker

SETTINGS_TEXT = (
    'Preparation is independent of builds. A selected batch pins plans, baseline and budget.\n'
    'Unknown impact, overlapping resources and unmet dependencies prevent parallel launch.\n'
    'Human gates remain for unresolved choices, exceptions and combined release.\n'
    'Pause stops new claims; Stop takes effect at a safe job boundary. Closing the board detaches.\n\n'
    'Runner: set FACTORY_RUNNER=claude before starting the worker, or [runner] agents="claude" in factory.toml.\n'
    'Generated test execution remains opt-in: FACTORY_RUN_TESTS=1. Missing checks block integration.\n'
    'Workspaces, logs and the database live under FACTORY_HOME. Worktrees are not OS sandboxes.')


class WorkflowScreen(Screen[int | None]):
    BINDINGS = [('escape', 'close', 'All runs'), ('r', 'refresh', 'Refresh'),
                ('m', 'menu', 'Menu'), ('q', 'quit_board', 'Quit board')]
    DEFAULT_CSS = '''
    WorkflowScreen { background: $background; }
    #wf-project { width: 1fr; }
    #wf-top { height: auto; }
    #wf-summary { height: auto; padding: 0 1; }
    #wf-controls { height: auto; }
    #wf-controls Button { margin-right: 1; }
    #wf-list { height: 1fr; min-height: 4; }
    #wf-detail-box { height: 2fr; min-height: 10; border: round $panel; padding: 0 1; }
    #wf-head { height: auto; max-height: 4; text-style: bold; }
    #wf-detail { height: 1fr; }
    #wf-text { height: auto; color: $text-muted; }
    #wf-answer { height: 5; margin-top: 1; }
    #wf-ticks { height: auto; max-height: 12; margin-top: 1; }
    #wf-buttons { height: auto; }
    #wf-buttons Button { margin-right: 1; }
    #wf-note { height: auto; color: $error; }
    '''

    def __init__(self, db_path: Path, project_ref: str | None = None):
        super().__init__()
        self.db_path, self.project_ref = db_path, project_ref
        self.items = {}
        self.selected = None
        self.data = {}
        self._signature = None
        self._loading = False
        self._acting = False
        # Drafts live here first (instant, survives navigation) and in the DB second
        # (survives a restart); the DB write happens off the UI thread.
        self._drafts: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id='wf-top'):
            yield Select([('All projects', '')], value='', id='wf-project', allow_blank=False,
                         compact=True)
        yield Static('', id='wf-summary', markup=False)
        yield Tabs(Tab('Overview', id='overview'), Tab('Needs you', id='inbox'),
                   Tab('Stories', id='stories'), Tab('Activity', id='activity'),
                   Tab('Settings', id='settings'), id='wf-tabs')
        with Horizontal(id='wf-controls'):
            yield Button('Interview', id='wf-intake', compact=True)
            yield Button('Propose backlog', id='wf-backlog', compact=True)
            yield Button('Propose batch', id='wf-propose', compact=True)
            yield Button('Pause new starts', id='wf-pause', compact=True)
            yield Button('Restart worker', id='wf-worker', variant='warning', compact=True)
        yield DataTable(id='wf-list', cursor_type='row')
        with Vertical(id='wf-detail-box'):
            # Pinned: what this is about never scrolls away from its options.
            yield Static('', id='wf-head', markup=False)
            with VerticalScroll(id='wf-detail'):
                yield Static('Select an item to see its context and next action.', id='wf-text',
                             markup=False)
                yield AnswerPicker(id='wf-picker')
                yield SelectionList[int](id='wf-ticks')
                yield TextArea(id='wf-answer', disabled=True)
            # Outside the scroll: however long the document, its actions stay on screen.
            with Horizontal(id='wf-buttons'):
                yield Button('Select an item', id='wf-primary', variant='primary', disabled=True,
                             compact=True)
                yield Button('Request changes', id='wf-secondary', compact=True)
                yield Button('Stop at safe boundary', id='wf-stop', disabled=True, compact=True)
            yield Static('', id='wf-note', markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title, self.sub_title = 'Factory', self.project_ref or 'All projects'
        self.query_one('#wf-list', DataTable).add_columns('Project', 'State', 'What')
        self.action_refresh()
        self.set_interval(2, self.action_refresh)

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 100, 'narrow')

    # ── read model → screen ─────────────────────────────────────────
    def action_refresh(self) -> None:
        if self._acting:
            return
        try:
            self.data = dashboard(self.project_ref, db_path=self.db_path)
            selector = self.query_one('#wf-project', Select)
            choices = [('All projects', '')] + [(p['name'] or p['slug'], p['slug']) for p in self.data['projects']]
            if getattr(self, '_choices', None) != choices:
                self._choices = choices
                selector.set_options(choices)
                selector.value = self.project_ref or ''
            self._summarize()
            self._fill()
        except Exception as exc:
            self._note(str(exc))

    def _summarize(self) -> None:
        data, stories = self.data, self.data['stories']
        queued = sum(j['status'] in ('queued', 'running') for j in data['jobs'])
        stuck = data['worker'] != 'active' and queued > 0
        worker = ('worker running' if data['worker'] == 'active'
                  else f'worker stopped with {queued} queued' if stuck else 'idle')
        waiting = len(self._inbox())
        counts = [f'{waiting} need you',
                  f"{sum(s['state'] == 'Working' for s in stories) + queued} working",
                  f"{sum(s['state'] == 'Ready' for s in stories)} ready",
                  f"{sum(s['state'] in ('Draft', 'Refining') for s in stories)} to refine",
                  f"{sum(s['state'] == 'Blocked' for s in stories)} blocked"]
        # The header and the selector already name the project; this line is the state.
        line = ' · '.join([worker.capitalize(), *counts])
        if data['paused']:
            line += ' · new starts paused'
        if not self.project_ref:
            line += '\nChoose a project above to interview, plan and launch.'
        self.query_one('#wf-summary', Static).update(line)
        self.query_one('#inbox', Tab).label = f'Needs you ({waiting})' if waiting else 'Needs you'
        self._apply_project_actions(stuck)

    def _apply_project_actions(self, stuck: bool) -> None:
        actions = {a.id: a for a in project_actions(self.data.get('project'))}
        for button in self.query_one('#wf-controls').query(Button):
            if button.id == 'wf-worker':
                button.display = stuck
                continue
            action = actions.get(button.id)
            button.display = action is not None
            if action:
                button.label, button.disabled = action.label, not action.enabled
                button.tooltip = action.reason if not action.enabled else None

    def _inbox(self) -> list[tuple]:
        """Everything waiting on the operator, decisions first."""
        data = self.data
        items = [('decision:' + d['id'], 'decision', d, d['project'], *decision_row(d))
                 for d in data['decisions']]
        items += [('batch:' + p['id'], 'batch', p, p['project'], p['status'].capitalize(),
                   f"Batch · {len(p['payload']['plan_ids'])} stories")
                  for p in data['proposals'] if p['status'] in ('proposed', 'blocked')]
        items += [('run:' + str(r['id']), 'run', r, r['project_slug'],
                   'Sign off' if r['status'] == 'waiting_human' else r['status'].capitalize(),
                   f"Run #{r['id']} {r['story_title']} · "
                   + (r.get('error') or r['current_stage'] or '').split('\n', 1)[0])
                  for r in data['runs'] if r['status'] != 'running']
        items += [('job:' + j['id'], 'job', j, j['project'], *job_row(j))
                  for j in data['jobs'] if j['status'] in ('failed', 'interrupted')]
        return items

    def _working(self) -> list[tuple]:
        data = self.data
        return ([('run:' + str(r['id']), 'run', r, r['project_slug'], 'Working',
                  f"Run #{r['id']} {r['story_title']} · {r['current_stage']}")
                 for r in data['runs'] if r['status'] == 'running']
                + [('job:' + j['id'], 'job', j, j['project'], *job_row(j))
                   for j in data['jobs'] if j['status'] in ('queued', 'running')]
                + [('batch:' + p['id'], 'batch', p, p['project'], p['status'].capitalize(),
                    f"Batch · {len(p['payload']['plan_ids'])} stories")
                   for p in data['proposals'] if p['status'] in ('launched', 'integrating')])

    def _events(self, limit: int) -> list[tuple]:
        return [('event:' + str(e['id']), 'event', e, e['project'], local_time(e['created_at']),
                 event_text(e)) for e in self.data['events'][:limit]]

    @on(Select.Changed, '#wf-project')
    def project_changed(self, event: Select.Changed) -> None:
        self.project_ref = str(event.value) or None
        self.app._project_filter = self.project_ref
        self.sub_title = self.project_ref or 'All projects'
        self._signature = None
        self.action_refresh()

    @on(Tabs.TabActivated, '#wf-tabs')
    def tab_changed(self) -> None:
        self._signature = None
        if self.data:
            self._fill()

    def _fill(self) -> None:
        tab = self.query_one('#wf-tabs', Tabs).active
        if tab == 'overview':
            items = self._inbox()[:3] + self._working()
            if step := next_step(self.data):
                items.append(('next', 'next', step, self.project_ref, 'Next', step.title))
            items += self._events(3)
        elif tab == 'inbox':
            items = self._inbox()
        elif tab == 'stories':
            items = [('story:' + str(s['id']), 'story', s, s['project'], s['state'],
                      f"#{s['id']} {s['title']}") for s in self.data['stories']]
        elif tab == 'activity':
            items = self._working() + self._events(100)
        else:
            items = [('settings', 'settings', {}, self.project_ref or 'All', 'Policy',
                      'Automatic routine work; human batch launch and release; strict scope and verification')]
        signature = json.dumps(items, sort_keys=True, default=str)
        if signature == self._signature:
            return
        self._signature = signature
        self.items = {key: (kind, row) for key, kind, row, *_ in items}
        table = self.query_one('#wf-list', DataTable)
        table.clear()
        for key, _kind, _row, project, state, title in items:
            width = max(20, self.size.width - 40)
            title = str(title).replace('\n', ' ')
            table.add_row(str(project or '')[:14], str(state)[:12],
                          title if len(title) <= width else title[:width - 1] + '…', key=key)
        keys = [i[0] for i in items]
        if self.selected in keys:
            table.move_cursor(row=keys.index(self.selected))
        elif items:
            table.move_cursor(row=0)
            self._select(items[0][0])
        else:
            self._select(None)

    @on(DataTable.RowHighlighted, '#wf-list')
    def highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._select(str(event.row_key.value))

    def _select(self, key) -> None:
        if key == self.selected:
            return
        self.selected = key
        self._loading = True
        area = self.query_one('#wf-answer', TextArea)
        primary = self.query_one('#wf-primary', Button)
        secondary = self.query_one('#wf-secondary', Button)
        stop = self.query_one('#wf-stop', Button)
        picker = self.query_one('#wf-picker', AnswerPicker)
        ticks = self.query_one('#wf-ticks', SelectionList)
        ticks.display = False
        secondary.display = stop.display = False  # shown only where they apply
        primary.disabled, stop.disabled, area.disabled = True, True, True
        stop.label = 'Stop at safe boundary'
        area.load_text('')
        picker.display, area.display = False, False
        kind, row = self.items.get(key, ('', {}))
        head, text, label = '', 'Nothing needs your input in this view.', 'Select an item'
        if kind == 'decision':
            actions = decision_actions(row)
            draft = self._drafts.get(row['id'], row.get('draft_text') or '')
            head, text, label = decision_head(row), decision_body(row), actions.primary
            if actions.picker:
                text += '\n\n↑↓ or 1-9 choose · Enter answers · Esc back to the list'
                picker.display = True
                picker.load(row['context']['question'], draft)
            elif actions.placeholder is not None:
                area.display, area.disabled = True, False
                area.placeholder = actions.placeholder
                area.load_text(draft)
            if actions.secondary:
                secondary.label, secondary.display = actions.secondary, True
            primary.disabled = False
        elif kind == 'next':
            head, text, label = row.title, row.explanation, row.label
            primary.disabled = False
        elif kind == 'story':
            head, text = f"Story #{row['id']} · {row['title']}", story_body(row)
            label = 'Refine again' if row['state'] in ('Ready', 'Blocked') else 'Refine story'
            primary.disabled = row['status'] != 'approved' or row['state'] in ('Refining', 'Needs input')
        elif kind == 'batch':
            stop.label, stop.display = 'Abandon batch', True
            stop.disabled = row['status'] in ('integrating',)
            stories = {s['id']: s for s in self.data['stories']}
            plans = [self.data['plans'][i] for i in row['payload']['plan_ids'] if i in self.data['plans']]
            head = f"Batch · {len(plans)} stories · {row['status']}"
            text = batch_body(row, self.data['plans'], stories)
            if row['status'] == 'proposed':
                ticks.clear_options()
                ticks.add_options([(batch_choice(p, stories), p['backlog_id'], True) for p in plans])
                ticks.display = True
                label, primary.disabled = self._launch_label(len(plans)), False
            elif row['status'] in ('launched', 'blocked'):
                label, primary.disabled = 'Verify combined candidate', False
            else:
                label = 'Integration in progress'
        elif kind == 'run':
            head = f"Run #{row['id']} · {row['story_title']}"
            text = f"{row['current_stage']}\n{row.get('error') or ''}"
            label, primary.disabled = 'Open run', False
        elif kind == 'job':
            head, text = job_row(row)[1], job_body(row)
            stop.display = row['status'] in ('running', 'queued')
            stop.disabled = not stop.display
            if row['status'] in ('failed', 'interrupted'):
                label = 'Retry' if row.get('retryable') else 'Reconcile stopped job'
                primary.disabled = False
        elif kind == 'event':
            head, text = event_text(row), event_body(row)
        elif kind == 'settings':
            head, text = 'How this board works', SETTINGS_TEXT
        self.query_one('#wf-head', Static).update(head)
        self.query_one('#wf-text', Static).update(text)
        primary.label = label
        self._loading = False

    @staticmethod
    def _launch_label(count: int) -> str:
        return f"Launch {count} {'story' if count == 1 else 'stories'}"

    @on(SelectionList.SelectedChanged, '#wf-ticks')
    def ticked(self, event: SelectionList.SelectedChanged) -> None:
        count = len(event.selection_list.selected)
        primary = self.query_one('#wf-primary', Button)
        primary.label, primary.disabled = self._launch_label(count), count == 0

    # ── drafts ──────────────────────────────────────────────────────
    @on(TextArea.Changed, '#wf-answer')
    def draft_changed(self) -> None:
        area = self.query_one('#wf-answer', TextArea)
        # load_text's Changed arrives after _loading is reset: a hidden or disabled box
        # is never the operator typing, and must not overwrite the picker's draft.
        if self._loading or not area.display or area.disabled:
            return
        kind, row = self.items.get(self.selected, ('', {}))
        if kind == 'decision' and row['kind'] != 'release':
            self._keep_draft(row['id'], area.text)

    @on(AnswerPicker.DraftChanged)
    def picker_draft_changed(self, event: AnswerPicker.DraftChanged) -> None:
        kind, row = self.items.get(self.selected, ('', {}))
        if kind == 'decision' and not self._loading:
            self._keep_draft(row['id'], event.value)

    def _keep_draft(self, decision_id: str, value: str) -> None:
        self._drafts[decision_id] = value
        self._save_draft(decision_id, value)

    @work(thread=True, group='draft')
    def _save_draft(self, decision_id: str, value: str) -> None:
        try:
            save_draft(decision_id, value, db_path=self.db_path)
        except Exception as exc:  # a locked DB must never take the board down
            self.app.call_from_thread(self._note, f'Draft not saved yet: {exc}')

    def _note(self, message: str) -> None:
        self.query_one('#wf-note', Static).update(message)

    # ── keys and buttons ────────────────────────────────────────────
    @on(DataTable.RowSelected, '#wf-list')
    def row_entered(self, event: DataTable.RowSelected) -> None:
        event.stop()
        kind, row = self.items.get(str(event.row_key.value), ('', {}))
        if kind == 'decision' and row['context'].get('question'):
            self.query_one('#wf-picker', AnswerPicker).focus_choices()

    @on(AnswerPicker.Answered)
    def picked(self, event: AnswerPicker.Answered) -> None:
        kind, row = self.items.get(self.selected, ('', {}))
        if kind == 'decision' and not self._acting:
            self._acting = True
            self._perform('wf-primary', kind, row, event.value)

    def on_key(self, event) -> None:
        if event.key == 'escape' and self.query_one('#wf-picker', AnswerPicker).query_one(
                'OptionList').has_focus:
            event.stop()
            self.query_one('#wf-list', DataTable).focus()

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        identity = event.button.id
        if self._acting:
            return
        kind, row = self.items.get(self.selected, ('', {}))
        if identity == 'wf-primary' and kind == 'run':
            self.dismiss(row['id'])
            return
        if identity in ('wf-intake', 'wf-backlog', 'wf-propose', 'wf-pause') and not self.project_ref:
            self.notify('Choose a project using the selector first.', severity='warning')
            return
        if identity == 'wf-intake' and str(event.button.label).startswith('Amend'):
            def amend(change: str | None) -> None:
                if change and not self._acting:
                    self._acting = True
                    self._perform('wf-amend', kind, row, change)
            self.app.push_screen(PromptScreen(f'Amend the {self.project_ref} brief: what changed?',
                                              'e.g. add gift wrapping at checkout'), amend)
            return
        text = self.query_one('#wf-answer', TextArea).text
        if kind == 'decision' and identity == 'wf-primary' and row['context'].get('question'):
            text = self.query_one('#wf-picker', AnswerPicker).value
            if not text:
                self._note('Choose an option, or write your answer under Other.')
                return
        if kind == 'batch' and identity == 'wf-primary' and row['status'] == 'proposed':
            # Read on the UI thread; _do runs in a worker thread and must not touch widgets.
            text = ','.join(map(str, self.query_one('#wf-ticks', SelectionList).selected))
        if identity == 'wf-secondary' and not text.strip():
            self._note('Say what should change first, in the box above.')
            self.query_one('#wf-answer', TextArea).focus()
            return
        self._acting = True
        self._perform(identity, kind, row, text)

    @work(thread=True, exclusive=True, group='workflow-action')
    def _perform(self, identity, kind, row, text) -> None:
        try:
            message, launch_worker = self._do(identity, kind, row, text)
            if launch_worker:
                start_worker(db_path=self.db_path)
            ok = True
        except Exception as exc:
            message, ok = str(exc), False
        # call_from_thread is the App's, not the Screen's: calling it on self crashed the board.
        self.app.call_from_thread(self._finished, message, ok)

    def _do(self, identity: str, kind: str, row, text: str) -> tuple[str, bool]:
        """Perform one action; (what happened, in the operator's words; start the worker?)."""
        db, project = self.db_path, self.project_ref
        if kind == 'next' and identity == 'wf-primary':
            identity = row.action
        if identity == 'wf-intake':
            start_refinement(project, db_path=db)
            return 'Interview started: its questions will appear in Needs you.', True
        if identity == 'wf-amend':
            start_refinement(project, db_path=db, amendment=text)
            return 'Amendment queued: what it affects will be asked in Needs you.', True
        if identity == 'wf-backlog':
            start_backlog(project, db_path=db)
            return 'Backlog proposal requested: it will appear in Needs you.', True
        if identity == 'wf-propose':
            propose_batch(project, db_path=db)
            return 'Batch proposed: review it in Needs you.', False
        if identity == 'wf-pause':
            pause_project(project, not self.data['paused'], db_path=db)
            return ('New starts resumed.' if self.data['paused'] else 'New starts paused.'), False
        if identity == 'wf-worker':
            return 'Worker restarted.', True
        if identity == 'wf-refine' or (identity == 'wf-primary' and kind == 'story'):
            if kind == 'next':
                ref, story, title = project, row.story_id, row.title.removeprefix('Refine story ')
            else:
                ref, story, title = row['project'], row['id'], f"#{row['id']} {row['title']}"
            start_refinement(ref, story, db_path=db)
            return f'Refining story {title}: its questions will appear in Needs you.', True
        if identity == 'wf-stop':
            if kind == 'batch':
                abandon_batch(row['id'], db_path=db)
                return 'Batch abandoned; its stories and history are kept.', False
            stop_job(row['id'], db_path=db)
            return 'Stop requested: it takes effect at the next safe boundary.', False
        if identity == 'wf-secondary' and kind == 'decision':
            request_changes(row['id'], text, db_path=db)
            return 'Changes sent: a new proposal will appear in Needs you.', True
        if identity == 'wf-primary' and kind == 'job':
            if row.get('retryable'):
                retry_job(row['id'], db_path=db)
                return f"Retrying: {row['subject']}.", True
            recover_job(row['id'], db_path=db)
            return 'Stopped job settled; nothing was repeated.', False
        if identity == 'wf-primary' and kind == 'decision':
            if row['kind'] == 'release':
                queue_release(row['id'], db_path=db)
                return 'Integration queued for this exact revision.', True
            if row['kind'] in ('backlog', 'brief'):
                answer(row['id'], 'approve', db_path=db)  # never the changes box
                return ('Backlog approved.' if row['kind'] == 'backlog'
                        else 'Brief approved: publishing it.'), True
            answer(row['id'], text, db_path=db)
            return 'Answer saved.', True
        if identity == 'wf-primary' and kind == 'batch':
            if row['status'] == 'proposed':
                ids = [int(i) for i in text.split(',') if i]
                launch_batch(row['id'], db_path=db, selected_ids=ids)
                count = len(ids)
                return f"Batch launched: {count} {'story' if count == 1 else 'stories'} queued to build.", True
            queue_integration(row['id'], db_path=db)
            return 'Combined verification queued.', True
        return 'Nothing to do for this item.', False

    def _finished(self, message: str, ok: bool) -> None:
        self._acting = False
        self.selected = None
        if ok:
            self._note('')
            self.app.notify(message, timeout=4)
        else:
            self._note(message)
            self.app.notify(message, severity='error', timeout=8)
        self._signature = None
        self.action_refresh()

    def action_menu(self) -> None:
        self.app.action_command_palette()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_quit_board(self) -> None:
        self.app.exit()
