import { useEffect, useMemo, useRef, useState } from 'react'
import { api, clock, ruDate } from '../api'
import type { Consultation, Drug, Field, IcdOption, Protocol, Quote, Segment, Template, Vital } from '../api'

const STATUS: Record<string, { text: string; cls: string }> = {
  ok: { text: 'готово', cls: 'pill-ok' },
  check: { text: 'проверьте', cls: 'pill-warn' },
  empty: { text: 'не прозвучало', cls: 'pill-warn' },
  danger: { text: 'предупреждение', cls: 'pill-danger' },
}

// Страница 5. Врач проверяет лист: слева разговор, справа поля с цитатами.
export default function Review({ visit, onChange }: { visit: Consultation; onChange: (v: Consultation) => void }) {
  const [template, setTemplate] = useState<Template | null>(null)
  const [active, setActive] = useState<number[]>([])
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const audio = useRef<HTMLAudioElement>(null)
  const fields = visit.fields ?? {}
  const segments = visit.segments ?? []

  useEffect(() => {
    api.template(visit.template).then(setTemplate).catch(() => {})
  }, [visit.template])

  const order = template ? template.fields.map((f) => f.id).filter((id) => fields[id]) : Object.keys(fields)
  const counts = useMemo(() => {
    const c = { ok: 0, check: 0, danger: 0 }
    for (const f of Object.values(fields)) {
      if (f.status === 'danger') c.danger += 1
      else if (f.status === 'ok') c.ok += 1
      else c.check += 1
    }
    return c
  }, [fields])

  const run = async (action: () => Promise<Consultation>) => {
    setError('')
    try {
      onChange(await action())
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const showQuotes = (quotes: Quote[]) => {
    const lines = quotes.map((q) => q.n)
    setActive(lines)
    if (lines.length) document.getElementById(`line-${lines[0]}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const playFrom = (seconds: number) => {
    const a = audio.current
    if (!a) return
    a.currentTime = Math.max(0, seconds - 0.3)
    a.play().catch(() => {})
  }

  const send = async () => {
    setSending(true)
    await run(() => api.send(visit.id))
    setSending(false)
  }

  const sent = visit.status === 'sent'

  return (
    <div className="review">
      <div className="review-head">
        <div>
          <h1>Лист консультации</h1>
          <p className="muted">
            {visit.patient.name}, приём {ruDate(visit.visit_date)} в {visit.created_at.slice(11, 16)}
          </p>
        </div>
        <div className="chips">
          {counts.danger > 0 && <span className="pill pill-danger">{counts.danger} предупр.</span>}
          {counts.check > 0 && <span className="pill pill-warn">{counts.check} проверить</span>}
          <span className="pill pill-ok">{counts.ok} готово</span>
        </div>
        <div className="grow" />
        <a className="btn" href={`#/visit/${visit.id}/contour`}>
          Контур данных
        </a>
        <a className="btn" href={`/api/consultations/${visit.id}/docx`}>
          Скачать Word
        </a>
        <button className="btn btn-primary" onClick={send} disabled={sending || visit.blocking.length > 0}>
          {sent ? 'Отправить в МИС ещё раз' : sending ? 'Отправляем…' : 'Отправить в МИС'}
        </button>
      </div>

      {sent && (
        <div className="alert alert-ok">
          Лист принят МИС в {visit.sent_at?.slice(11, 16)}, номер записи {visit.mis_id}.
        </div>
      )}
      {visit.blocking.length > 0 && (
        <div className="alert alert-danger">Перед отправкой в МИС разберите красное предупреждение в назначениях.</div>
      )}
      {error && <div className="alert alert-danger">{error}</div>}

      <div className="review-body">
        <section className="card talk">
          <div className="card-head">
            <h2>Разговор</h2>
            {visit.timings && <span className="mono muted">{clock(visit.timings.audio)}</span>}
          </div>
          {visit.has_audio && <audio ref={audio} controls preload="metadata" src={`/api/consultations/${visit.id}/audio`} />}
          {(visit.gaps ?? []).map((g) => (
            <div className="alert alert-warn" key={g.from}>
              С {clock(g.from)} до {clock(g.to)} текста нет. Если в это время говорили,{' '}
              <button className="link" onClick={() => playFrom(g.from)}>
                прослушайте этот участок
              </button>
              .
            </div>
          ))}
          <div className="lines">
            {segments.map((s) => (
              <Line key={s.n} s={s} on={active.includes(s.n)} visitId={visit.id} onPlay={playFrom} onChange={onChange} />
            ))}
          </div>
          <p className="note">
            Речь распознана на сервере клиники. Казахские реплики переведены, поля заполняются по переводу. Нажмите на время, чтобы прослушать фразу, и на роль, чтобы её поменять.
          </p>
        </section>

        <section className="sheet">
          {order.map((id) => (
            <FieldCard
              key={id}
              id={id}
              f={fields[id]}
              visit={visit}
              onQuotes={showQuotes}
              onSave={(patch) => run(() => api.editField(visit.id, id, patch))}
              onConfirm={(note) => run(() => api.confirmField(visit.id, id, note))}
            />
          ))}
          <ProtocolBlock visit={visit} onChange={onChange} />
          {visit.timings && (
            <p className="muted small">
              Запись {clock(visit.timings.audio)}. Обработка {Math.round(visit.timings.total)} с: распознавание{' '}
              {Math.round(visit.timings.asr)} с
              {visit.timings.translate ? `, перевод с казахского ${Math.round(visit.timings.translate)} с` : ''}, поля{' '}
              {Math.round(visit.timings.fields)} с.
            </p>
          )}
        </section>
      </div>
    </div>
  )
}

function Line({
  s,
  on,
  visitId,
  onPlay,
  onChange,
}: {
  s: Segment
  on: boolean
  visitId: number
  onPlay: (t: number) => void
  onChange: (v: Consultation) => void
}) {
  const flip = async () => {
    await api.setRole(visitId, s.n, s.role === 'doctor' ? 'patient' : 'doctor')
    onChange(await api.get(visitId))
  }
  return (
    <div id={`line-${s.n}`} className={'line' + (on ? ' on' : '')}>
      <button className="line-time mono" onClick={() => onPlay(s.start)} title="Прослушать с этого места">
        {clock(s.start)}
      </button>
      <div>
        <button className={'role ' + s.role} onClick={flip} title="Поменять роль">
          {s.role === 'doctor' ? 'Врач' : 'Пациент'}
        </button>
        {s.lang === 'kk' && <span className="lang">қаз</span>}
        <div className="line-text">{s.text}</div>
        {s.ru && (
          <div className={'line-ru' + (s.untranslated ? ' warn' : '')}>
            {s.untranslated ? 'перевод неполный: ' : 'перевод: '}
            {s.ru}
          </div>
        )}
        {s.fixes && s.fixes.length > 0 && (
          <div className="fixes">
            исправлено по словарю: {s.fixes.map((f) => `${f.from} → ${f.to}`).join(', ')}
          </div>
        )}
      </div>
    </div>
  )
}

function Quotes({ quotes, onQuotes }: { quotes: Quote[]; onQuotes: (q: Quote[]) => void }) {
  if (!quotes?.length) return null
  return (
    <div className="quotes">
      {quotes.map((q) => (
        <button key={q.n} className="quote" onClick={() => onQuotes([q])} title="Показать в разговоре">
          <span className="mono">{clock(q.start)}</span> «{q.text.length > 90 ? q.text.slice(0, 90) + '…' : q.text}»
        </button>
      ))}
    </div>
  )
}

type Patch = { value?: string; items?: unknown[]; icd?: IcdOption }

// Диктовка: врач наговаривает поле вместо набора на клавиатуре.
// Запись уходит на сервер клиники, распознаётся там же и возвращается текстом.
function Dictate({
  visitId,
  field,
  onResult,
}: {
  visitId: number
  field: string
  onResult: (r: { text: string; items?: (Vital | Drug)[] }) => void
}) {
  const [phase, setPhase] = useState<'idle' | 'recording' | 'busy'>('idle')
  const [error, setError] = useState('')
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])

  useEffect(
    () => () => {
      const rec = recorder.current
      if (rec && rec.state !== 'inactive') {
        rec.onstop = null
        rec.stop()
      }
      rec?.stream.getTracks().forEach((t) => t.stop())
    },
    [],
  )

  const start = async () => {
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find((m) => MediaRecorder.isTypeSupported(m))
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
      chunks.current = []
      rec.ondataavailable = (e) => e.data.size && chunks.current.push(e.data)
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        const type = rec.mimeType || 'audio/webm'
        setPhase('busy')
        try {
          onResult(await api.dictate(visitId, field, new Blob(chunks.current, { type }), type.includes('mp4') ? 'd.m4a' : 'd.webm'))
        } catch (e) {
          setError((e as Error).message)
        }
        setPhase('idle')
      }
      rec.start()
      recorder.current = rec
      setPhase('recording')
    } catch {
      setError('Нет доступа к микрофону.')
    }
  }

  return (
    <>
      <button
        type="button"
        className={'btn dictate' + (phase === 'recording' ? ' rec' : '')}
        onClick={() => (phase === 'recording' ? recorder.current?.stop() : start())}
        disabled={phase === 'busy'}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="9" y="3" width="6" height="11" rx="3" />
          <path d="M5 11a7 7 0 0 0 14 0" />
          <path d="M12 18v3" />
        </svg>
        {phase === 'idle' && 'Диктовать'}
        {phase === 'recording' && 'Остановить'}
        {phase === 'busy' && 'Распознаём…'}
      </button>
      {error && <span className="warn-note inline">{error}</span>}
    </>
  )
}

function FieldCard({
  id,
  f,
  visit,
  onQuotes,
  onSave,
  onConfirm,
}: {
  id: string
  f: Field
  visit: Consultation
  onQuotes: (q: Quote[]) => void
  onSave: (patch: Patch) => Promise<void>
  onConfirm: (note: string) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(f.value)
  const [rows, setRows] = useState<(Vital | Drug)[]>(f.items ?? [])
  const status = STATUS[f.status] ?? STATUS.check
  const danger = f.flags.filter((x) => x.level === 'danger')
  const warns = f.flags.filter((x) => x.level !== 'danger')
  const acknowledged = danger.length > 0 && !visit.blocking.some((b) => b.field === id)

  const open = () => {
    setText(f.kind === 'date_future' || f.kind === 'date_past' ? (f.raw ?? f.value) : f.value)
    setRows(f.items ? f.items.map((x) => ({ ...x })) : [])
    setEditing(true)
  }
  const dictated = (r: { text: string; items?: (Vital | Drug)[] }) => {
    if (f.kind === 'prescriptions') {
      const added = ((r.items as Drug[]) ?? []).map(({ drug, dose, frequency, duration }) => ({ drug, dose, frequency, duration }))
      setRows((prev) => [...(prev as Drug[]).filter((x) => x.drug.trim()), ...added])
    } else if (f.kind === 'vitals') {
      const heard = new Map(((r.items as Vital[]) ?? []).filter((v) => v.value).map((v) => [v.key, v.value]))
      setRows((prev) => (prev as Vital[]).map((v) => (heard.has(v.key) ? { ...v, value: heard.get(v.key)! } : v)))
    } else {
      const spoken = f.kind === 'diagnosis' ? r.text.replace(/[.!]+$/, '') : r.text
      setText((prev) => (prev.trim() ? prev.trim() + ' ' + spoken : spoken))
    }
  }
  const save = async () => {
    if (f.kind === 'vitals' || f.kind === 'prescriptions') {
      const items = f.kind === 'prescriptions' ? (rows as Drug[]).filter((r) => r.drug.trim()) : rows
      await onSave({ items })
    } else {
      await onSave({ value: text })
    }
    setEditing(false)
  }

  return (
    <article className={'card field ' + f.status + (f.kind === 'vitals' || f.kind === 'prescriptions' ? ' wide' : '')}>
      <div className="card-head">
        <h3>{f.title}</h3>
        {f.edited && <span className="tag">исправлено врачом</span>}
        <div className="grow" />
        <span className={'pill ' + (acknowledged ? 'pill-muted' : status.cls)}>{acknowledged ? 'подтверждено врачом' : status.text}</span>
        {!editing && (
          <button className="link" onClick={open}>
            Изменить
          </button>
        )}
      </div>

      {!editing && f.kind === 'vitals' && (
        <div className="vitals">
          {(f.items as Vital[]).map((v) => (
            <div key={v.key} className={'vital' + (v.note ? ' warn' : '') + (v.value ? '' : ' none')}>
              <span>
                {v.title}, {v.unit}
              </span>
              <b className="mono">{v.value || '—'}</b>
            </div>
          ))}
        </div>
      )}

      {!editing && f.kind === 'prescriptions' && (
        <Prescriptions items={(f.items as Drug[]) ?? []} flagged={danger.map((d) => d.drug ?? '')} />
      )}

      {!editing && (f.kind === 'text' || f.kind === 'diagnosis') && (
        <p className={'value' + (f.value ? '' : ' none')}>{f.value || 'Не прозвучало в разговоре. Поле оставлено пустым.'}</p>
      )}

      {!editing && (f.kind === 'date_future' || f.kind === 'date_past') && (
        <div className="date-row">
          <b className="date">{f.date ? ruDate(f.date) : f.value || '—'}</b>
          {f.value && (
            <span className="muted">
              прозвучало «{f.raw ?? f.value}»{f.rule ? `, ${f.rule}` : ''}
            </span>
          )}
          {!f.value && <span className="value none">Не прозвучало в разговоре.</span>}
        </div>
      )}

      {!editing && f.pregnancy && (
        <div className="computed">
          <div>
            <span>Срок беременности</span>
            <b>
              {f.pregnancy.weeks} нед. {f.pregnancy.days} дн.
            </b>
          </div>
          <div>
            <span>Предполагаемая дата родов</span>
            <b>{ruDate(f.pregnancy.edd)}</b>
          </div>
          <small>Рассчитано формулой, не моделью: {f.pregnancy.rule}.</small>
        </div>
      )}

      {!editing && f.kind === 'diagnosis' && f.value && <IcdPicker f={f} onPick={(icd) => onSave({ icd })} />}

      {editing && f.kind === 'vitals' && (
        <div className="vitals">
          {(rows as Vital[]).map((v, i) => (
            <label key={v.key} className="vital edit">
              <span>
                {v.title}, {v.unit}
              </span>
              <input
                className="input mono"
                value={v.value}
                onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}
              />
            </label>
          ))}
        </div>
      )}

      {editing && f.kind === 'prescriptions' && (
        <div className="rx-edit">
          {(rows as Drug[]).map((r, i) => (
            <div className="rx-row" key={i}>
              {(['drug', 'dose', 'frequency', 'duration'] as const).map((k) => (
                <input
                  key={k}
                  className="input"
                  aria-label={{ drug: 'Препарат', dose: 'Доза', frequency: 'Кратность', duration: 'Срок' }[k]}
                  placeholder={{ drug: 'Препарат', dose: 'Доза', frequency: 'Кратность', duration: 'Срок' }[k]}
                  value={r[k]}
                  onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))}
                />
              ))}
              <button className="link danger" onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label="Удалить препарат">
                Удалить
              </button>
            </div>
          ))}
          <button className="link" onClick={() => setRows([...rows, { drug: '', dose: '', frequency: '', duration: '' }])}>
            Добавить препарат
          </button>
        </div>
      )}

      {editing && f.kind !== 'vitals' && f.kind !== 'prescriptions' && (
        <textarea className="input area" value={text} onChange={(e) => setText(e.target.value)} rows={3} autoFocus aria-label={f.title} />
      )}

      {editing && (
        <div className="actions">
          <Dictate visitId={visit.id} field={id} onResult={dictated} />
          <button className="btn btn-primary" onClick={save}>
            Сохранить
          </button>
          <button className="btn" onClick={() => setEditing(false)}>
            Отмена
          </button>
        </div>
      )}

      {!editing &&
        danger.map((d) => (
          <div className="alert alert-danger row" key={d.text}>
            <span className="grow">{d.text}</span>
            {!acknowledged && (
              <>
                <button className="btn btn-danger" onClick={open}>
                  Изменить назначение
                </button>
                <button className="btn" onClick={() => onConfirm('')}>
                  Оставить, подтверждаю
                </button>
              </>
            )}
          </div>
        ))}

      {!editing && warns.map((w) => (
        <p className="warn-note" key={w.text}>
          {w.text}
        </p>
      ))}

      {!editing && f.status === 'empty' && f.kind === 'text' && (
        <div className="actions">
          <button className="btn" onClick={open}>
            Заполнить или продиктовать
          </button>
          <button className="btn" onClick={() => onConfirm('Без особенностей.')}>
            Без особенностей
          </button>
        </div>
      )}

      {!editing && f.status === 'check' && f.value && (
        <div className="actions">
          <button className="btn" onClick={() => onConfirm('')}>
            Всё верно
          </button>
        </div>
      )}

      {!editing && <Quotes quotes={f.quotes} onQuotes={onQuotes} />}
    </article>
  )
}

function Prescriptions({ items, flagged }: { items: Drug[]; flagged: string[] }) {
  if (!items.length) return <p className="value none">Назначения не прозвучали.</p>
  return (
    <table className="rx">
      <thead>
        <tr>
          <th>Препарат</th>
          <th>Доза</th>
          <th>Кратность</th>
          <th>Срок</th>
        </tr>
      </thead>
      <tbody>
        {items.map((p, i) => (
          <tr key={i} className={flagged.includes(p.drug) ? 'flagged' : ''}>
            <td>
              <b>{p.drug}</b>
            </td>
            <td>{p.dose || <span className="muted">не прозвучало</span>}</td>
            <td>{p.frequency || <span className="muted">не прозвучало</span>}</td>
            <td>{p.duration || <span className="muted">не прозвучало</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function IcdPicker({ f, onPick }: { f: Field; onPick: (icd: IcdOption) => void }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<IcdOption[]>([])
  const icd = f.icd

  useEffect(() => {
    if (!open || query.trim().length < 2) return
    const timer = setTimeout(() => api.icd(query).then(setFound).catch(() => {}), 200)
    return () => clearTimeout(timer)
  }, [query, open])

  const options = query.trim().length >= 2 ? found : (icd?.options ?? [])

  return (
    <div className="icd">
      <div className="icd-line">
        <span className="icd-code mono">{icd?.code || 'код не выбран'}</span>
        <span>{icd?.name}</span>
        {icd?.source && <span className="muted small">{icd.source}</span>}
        <button className="link" onClick={() => setOpen(!open)}>
          {open ? 'Закрыть' : 'Выбрать код МКБ-10'}
        </button>
      </div>
      {open && (
        <div className="icd-pick">
          <input
            className="input"
            placeholder="Название болезни или код, например J03"
            aria-label="Поиск по МКБ-10"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          {options.map((o) => (
            <button
              key={o.code}
              className="icd-option"
              onClick={() => {
                onPick(o)
                setOpen(false)
              }}
            >
              <span className="mono">{o.code}</span>
              {o.name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Диагностика по клиническому протоколу МЗ РК: только текст протокола, решение за врачом.
function ProtocolBlock({ visit, onChange }: { visit: Consultation; onChange: (v: Consultation) => void }) {
  const [p, setP] = useState<Protocol | null>(null)
  const code = visit.fields?.diagnosis?.icd?.code ?? ''
  const stamp = JSON.stringify([
    code,
    visit.fields?.plan?.value,
    visit.fields?.status?.value,
    visit.fields?.vitals?.value,
    visit.fields?.recommendations?.value,
    visit.fields?.lmp?.date,
  ])

  useEffect(() => {
    if (!code) {
      setP(null)
      return
    }
    api.protocol(visit.id).then(setP).catch(() => setP(null))
  }, [visit.id, stamp, code])

  if (!p || !code) return null
  if (!p.found) {
    return (
      <article className="card field wide protocol">
        <div className="card-head">
          <h3>Диагностика по клиническому протоколу МЗ РК</h3>
        </div>
        <p className="muted">
          Для кода {code} протокол ещё не подключён. Подключены: {(p.available ?? []).map((a) => a.title).join(', ') || 'нет'}.
        </p>
      </article>
    )
  }
  const missing = (p.sections ?? []).reduce((n, s) => n + s.items.filter((i) => !i.present && !i.note).length, 0)
  return (
    <article className="card field wide protocol">
      <div className="card-head">
        <h3>Диагностика по клиническому протоколу МЗ РК</h3>
        {!p.verified_by_doctor && <span className="pill pill-warn">черновик, не сверен врачом</span>}
        {missing > 0 && <span className="pill pill-muted">нет в листе: {missing}</span>}
      </div>
      <p className="protocol-title">
        <b>{p.title}</b>
        <span className="muted">
          {' '}
          {p.version}, {p.approved}.{' '}
        </span>
        {p.source && (
          <a href={p.source} target="_blank" rel="noreferrer">
            Текст протокола
          </a>
        )}
      </p>
      {p.need && <div className="alert alert-warn">{p.need}</div>}
      {p.visit && (
        <p className="protocol-visit">
          Срок {p.visit.weeks} нед. {p.visit.days} дн. {p.visit.exact ? 'По протоколу сейчас:' : 'Ближайшее по протоколу:'}{' '}
          <b>{p.visit.title}</b>
        </p>
      )}
      <div className="protocol-grid">
        {p.sections?.map((s) => (
          <div key={s.key}>
            <h4>{s.title}</h4>
            <ul>
              {s.items.map((i) => (
                <li key={i.text} className={i.present ? 'present' : i.note ? 'optional' : ''}>
                  <span className="check" aria-hidden="true">
                    {i.present ? '✓' : ''}
                  </span>
                  <span className="grow">
                    {i.text}
                    {i.note && <small> ({i.note})</small>}
                  </span>
                  {i.present ? (
                    <span className="muted small">есть в листе</span>
                  ) : (
                    <button className="link" onClick={async () => onChange(await api.protocolAdd(visit.id, s.key, i.text))}>
                      В план
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <p className="note">Справочно, по тексту протокола. Диагноз и назначения определяет врач.</p>
    </article>
  )
}
