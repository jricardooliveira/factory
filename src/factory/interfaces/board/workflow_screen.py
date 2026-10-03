"""The human's home: decisions first, independent stories and meaningful activity."""
from __future__ import annotations

import json
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Select, Static, Tab, Tabs, TextArea

from factory.runs.batches import abandon_batch, launch_batch, propose_batch, queue_integration, queue_release
from factory.runs.dashboard import dashboard, pause_project, recover_job, stop_job
from factory.runs.refinement import answer, save_draft, start_backlog, start_refinement
from factory.runs.worker import start_worker


class WorkflowScreen(Screen[int | None]):
    BINDINGS = [('escape', 'close', 'Run details'), ('r', 'refresh', 'Refresh'),
                ('m', 'menu', 'Menu'), ('q', 'quit_board', 'Quit board')]
    DEFAULT_CSS = '''
    WorkflowScreen { background: $background; overflow-y: auto; }
    #wf-project { width: 1fr; }
    #wf-top { height: auto; }
    #wf-summary { height: auto; padding: 1; }
    #wf-list { height: 1fr; min-height: 4; }
    #wf-detail { height: 2fr; min-height: 8; border: round $panel; padding: 0 1; }
    #wf-text { height: auto; }
    #wf-answer { height: 5; margin-top: 1; }
    #wf-buttons { height: auto; }
    #wf-controls { height: auto; }
    #wf-note { height: auto; color: $warning; }
    .narrow #wf-controls { layout: grid; grid-size: 2; grid-gutter: 0; }
    .narrow #wf-buttons { layout: grid; grid-size: 2; grid-gutter: 0; }
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id='wf-top'):
            yield Select([('All projects', '')], value='', id='wf-project', allow_blank=False)
        yield Static('', id='wf-summary', markup=False)
        yield Tabs(Tab('Overview', id='overview'), Tab('Needs you', id='inbox'),
                   Tab('Stories', id='stories'), Tab('Activity', id='activity'),
                   Tab('Settings', id='settings'), id='wf-tabs')
        with Horizontal(id='wf-controls'):
            yield Button('Interview', id='wf-intake')
            yield Button('Propose backlog', id='wf-backlog')
            yield Button('Propose batch', id='wf-propose')
            yield Button('Pause new starts', id='wf-pause')
            yield Button('Start worker', id='wf-worker')
        yield DataTable(id='wf-list', cursor_type='row')
        with VerticalScroll(id='wf-detail'):
            yield Static('Select an item to see its context and next action.', id='wf-text', markup=False)
            yield TextArea(id='wf-answer', disabled=True)
            with Horizontal(id='wf-buttons'):
                yield Button('Select an item', id='wf-primary', variant='primary', disabled=True)
                yield Button('Stop at safe boundary', id='wf-stop', disabled=True)
                yield Button('Run details', id='wf-runs')
            yield Static('', id='wf-note', markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one('#wf-list', DataTable).add_columns('Project / item', 'State', 'What is happening')
        self.action_refresh()
        self.set_interval(2, self.action_refresh)

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 100, 'narrow')

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
            stories = self.data['stories']
            counts = {state: sum(s['state'] == state for s in stories)
                      for state in ('Refining', 'Ready', 'Working', 'Needs input', 'Blocked')}
            scope = self.project_ref or 'All projects'
            self.query_one('#wf-summary', Static).update(
                f"{scope} · Worker {self.data['worker']}\n" + '  |  '.join(f'{k}: {v}' for k, v in counts.items())
                + '\nInterview → Refine → Select batch → Build & verify → Human release'
                + ('\nNew starts paused. Running calls finish at their next safe boundary.' if self.data['paused'] else '')
                + ('\nNo stories yet. Interview → approve brief → Menu: propose backlog → refine stories.' if not stories else ''))
            self.query_one('#wf-pause', Button).label = 'Resume new starts' if self.data['paused'] else 'Pause new starts'
            self._fill()
        except Exception as exc:
            self.query_one('#wf-note', Static).update(str(exc))

    @on(Select.Changed, '#wf-project')
    def project_changed(self, event: Select.Changed) -> None:
        self.project_ref = str(event.value) or None
        self.app._project_filter = self.project_ref
        self._signature = None
        self.action_refresh()

    @on(Tabs.TabActivated, '#wf-tabs')
    def tab_changed(self) -> None:
        self._signature = None
        if self.data:
            self._fill()

    def _fill(self) -> None:
        tab = self.query_one('#wf-tabs', Tabs).active
        items = []
        if tab in ('overview', 'inbox'):
            items += [('decision:' + d['id'], 'decision', d, d['project'], d['kind'], d['question'])
                      for d in self.data['decisions']]
            items += [('batch:' + p['id'], 'batch', p, p['project'], p['status'], 'Compatible batch / combined candidate')
                      for p in self.data['proposals'] if p['status'] in ('proposed', 'launched', 'blocked', 'integrating')]
            items += [('run:' + str(r['id']), 'run', r, r['project_slug'], r['status'],
                       f"Run #{r['id']} · {r['current_stage']} · {r.get('error') or r['story_title']}")
                      for r in self.data['runs'] if tab == 'overview' or r['status'] != 'running']
            items += [('job:' + j['id'], 'job', j, j['project'], j['status'], j['error'] or j['kind'])
                      for j in self.data['jobs'] if j['status'] in ('failed', 'interrupted')]
        elif tab == 'stories':
            items = [('story:' + str(s['id']), 'story', s, s['project'], s['state'], s['title'])
                     for s in self.data['stories']]
        elif tab == 'activity':
            items = [('event:' + str(e['id']), 'event', e, e['project'], e['created_at'][11:19], e['message'])
                     for e in self.data['events'][:100]]
            items = [('job:' + j['id'], 'job', j, j['project'], j['status'], j['kind'])
                     for j in self.data['jobs'] if j['status'] in ('queued', 'running')] + items
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
            width = max(20, self.size.width - 45)
            title = str(title).replace('\n', ' ')
            table.add_row(str(project)[:18], str(state)[:20],
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
        stop = self.query_one('#wf-stop', Button)
        primary.disabled, stop.disabled, area.disabled = True, True, True
        stop.label = 'Stop at safe boundary'
        area.load_text('')
        kind, row = self.items.get(key, ('', {}))
        text, label = 'Nothing needs your input in this view.', 'Select an item'
        if kind == 'decision':
            context = row['context']
            question = context.get('question', {})
            options = '\n'.join(f"{i}. {o['label']} — {o.get('description', '')}"
                                for i, o in enumerate(question.get('options', []), 1))
            text = row['question'] + '\n\n'
            if question:
                text += context.get('why', '') + '\n' + options + '\n\n' + context.get('effect', '')
                text += '\nType an option number, your answer, or “you decide” to delegate.'
            else:
                text += json.dumps(context, indent=2, ensure_ascii=False)
            label = 'Approve integration' if row['kind'] == 'release' else 'Submit answer'
            area.disabled = row['kind'] == 'release'
            area.load_text(row.get('draft_text') or '')
            primary.disabled = False
        elif kind == 'story':
            text = f"Story #{row['id']} · {row['title']}\n{row['request']}\n\nState: {row['state']}"
            if row['plan']:
                text += '\n\nPlanned impact:\n' + json.dumps(row['plan'], indent=2)
            label, primary.disabled = 'Refine story', row['status'] != 'approved'
        elif kind == 'batch':
            stop.label = 'Abandon batch'
            stop.disabled = row['status'] in ('integrating',)
            plans = [self.data['plans'].get(i, {'id': i}) for i in row['payload']['plan_ids']]
            text = 'Batch ' + row['id'] + '\n' + json.dumps({**row['payload'], 'plans': plans}, indent=2)
            if row['status'] == 'proposed':
                label, primary.disabled, area.disabled = 'Launch selected stories', False, False
                area.load_text(', '.join(str(p['backlog_id']) for p in plans if 'backlog_id' in p))
                text += '\n\nEdit the backlog IDs below to deselect stories. Launch authorizes model usage within the displayed allowances.'
            elif row['status'] in ('launched', 'blocked'):
                label, primary.disabled = 'Verify combined candidate', False
            else:
                label = 'Integration in progress'
        elif kind == 'run':
            text = f"Run #{row['id']} · {row['story_title']}\n{row['current_stage']}\n{row.get('error') or ''}"
            label, primary.disabled = 'Open run / decision', False
        elif kind == 'job':
            text = json.dumps(row, indent=2)
            stop.disabled = row['status'] not in ('running', 'queued')
            if row['status'] in ('failed', 'interrupted'):
                label, primary.disabled = 'Reconcile stopped job', False
                text += '\n\nWork is retained. Inspect the saved error/workspace before retrying; a lost lease is never automatically repeated.'
        elif kind == 'event':
            text = row['message'] + '\n' + json.dumps(row['details'], indent=2)
        elif kind == 'settings':
            text = ('Preparation is independent of builds. A selected batch pins plans, baseline and budget.\n'
                    'Unknown impact, overlapping resources and unmet dependencies prevent parallel launch.\n'
                    'Human gates remain for unresolved choices, exceptions and combined release.\n'
                    'Pause stops new claims; Stop takes effect at a safe job boundary. Closing the board detaches.\n\n'
                    'Runner: set FACTORY_RUNNER=claude before starting the worker, or [runner] agents="claude" in factory.toml.\n'
                    'Generated test execution remains opt-in: FACTORY_RUN_TESTS=1. Missing checks block integration.\n'
                    'Workspaces, logs and the database live under FACTORY_HOME. Worktrees are not OS sandboxes.')
        self.query_one('#wf-text', Static).update(text)
        primary.label = label
        self._loading = False

    @on(TextArea.Changed, '#wf-answer')
    def draft_changed(self) -> None:
        if self._loading:
            return
        kind, row = self.items.get(self.selected, ('', {}))
        if kind == 'decision' and row['kind'] != 'release':
            save_draft(row['id'], self.query_one('#wf-answer', TextArea).text, db_path=self.db_path)

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        identity = event.button.id
        if identity == 'wf-runs':
            self.dismiss(None)
            return
        if self._acting:
            return
        kind, row = self.items.get(self.selected, ('', {}))
        if identity == 'wf-primary' and kind == 'run':
            self.dismiss(row['id'])
            return
        if identity in ('wf-intake', 'wf-backlog', 'wf-propose', 'wf-pause') and not self.project_ref:
            self.notify('Choose a project using the selector first.', severity='warning')
            return
        text = self.query_one('#wf-answer', TextArea).text
        self._acting = True
        self._perform(identity, kind, row, text)

    @work(thread=True, exclusive=True, group='workflow-action')
    def _perform(self, identity, kind, row, text) -> None:
        try:
            db = self.db_path
            launch_worker = False
            if identity == 'wf-intake':
                start_refinement(self.project_ref, db_path=db)
                launch_worker = True
            elif identity == 'wf-backlog':
                start_backlog(self.project_ref, db_path=db)
                launch_worker = True
            elif identity == 'wf-propose':
                propose_batch(self.project_ref, db_path=db)
            elif identity == 'wf-pause':
                pause_project(self.project_ref, not self.data['paused'], db_path=db)
            elif identity == 'wf-worker':
                launch_worker = True
            elif identity == 'wf-stop':
                if kind == 'batch':
                    abandon_batch(row['id'], db_path=db)
                else:
                    stop_job(row['id'], db_path=db)
            elif identity == 'wf-primary':
                if kind == 'job':
                    recover_job(row['id'], db_path=db)
                elif kind == 'story':
                    start_refinement(row['project'], row['id'], db_path=db)
                    launch_worker = True
                elif kind == 'decision':
                    if row['kind'] == 'release':
                        queue_release(row['id'], db_path=db)
                        launch_worker = True
                    else:
                        answer(row['id'], text, db_path=db)
                        launch_worker = True
                elif kind == 'batch':
                    if row['status'] == 'proposed':
                        ids = [int(i.strip()) for i in text.split(',') if i.strip()]
                        launch_batch(row['id'], db_path=db, selected_ids=ids)
                    else:
                        queue_integration(row['id'], db_path=db)
                    launch_worker = True
            if launch_worker:
                start_worker(db_path=db)
            message = 'Action saved. The board will show the resulting state.'
        except Exception as exc:
            message = str(exc)
        self.call_from_thread(self._finished, message)

    def _finished(self, message: str) -> None:
        self._acting = False
        self.selected = None
        self.query_one('#wf-note', Static).update(message)
        self._signature = None
        self.action_refresh()

    def action_menu(self) -> None:
        self.app.action_command_palette()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_quit_board(self) -> None:
        self.app.exit()
