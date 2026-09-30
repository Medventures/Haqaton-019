import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Consultation } from '../api'
import { go } from '../App'
import Review from './Review'

const STEPS = [
  { key: 'asr', title: 'Распознавание речи', hint: 'whisper на сервере клиники' },
  { key: 'mask', title: 'Маскирование персональных данных', hint: 'ФИО, ИИН, телефоны, адреса' },
  { key: 'translate', title: 'Перевод казахских реплик', hint: 'только для казахской и смешанной речи' },
  { key: 'fields', title: 'Заполнение полей листа', hint: 'локальная модель' },
  { key: 'checks', title: 'Расчёты и проверки', hint: 'даты, коды МКБ-10, аллергии' },
]

// Страницы 4 и 5. Пока запись обрабатывается, показываем шаги; затем лист для проверки.
export default function Visit({ id }: { id: number }) {
  const [visit, setVisit] = useState<Consultation | null>(null)
  const [error, setError] = useState('')
  const [waited, setWaited] = useState(0)

  useEffect(() => {
    let stop = false
    let timer = 0
    const started = Date.now()
    const poll = async () => {
      try {
        const v = await api.get(id)
        if (stop) return
        setVisit(v)
        if (v.status === 'new') {
          go(`visit/${id}/record`)
          return
        }
        if (v.status === 'processing') {
          setWaited(Math.round((Date.now() - started) / 1000))
          timer = window.setTimeout(poll, 1000)
        }
      } catch (e) {
        if (!stop) setError((e as Error).message)
      }
    }
    poll()
    return () => {
      stop = true
      clearTimeout(timer)
    }
  }, [id])

  if (error) return <div className="narrow"><div className="alert alert-danger">{error}</div></div>
  if (!visit) return <p className="muted narrow">Загружаем приём…</p>

  if (visit.status === 'error') {
    return (
      <div className="narrow">
        <h1>Не удалось обработать запись</h1>
        <div className="alert alert-danger">{visit.error}</div>
        <div className="actions">
          <button
            className="btn btn-primary"
            onClick={async () => {
              await api.reprocess(id)
              window.location.reload()
            }}
          >
            Обработать ещё раз
          </button>
          <a className="btn" href={`#/visit/${id}/record`}>
            Записать заново
          </a>
        </div>
      </div>
    )
  }

  if (visit.status === 'processing') {
    const current = STEPS.findIndex((s) => s.key === visit.step)
    return (
      <div className="narrow">
        <h1>Заполняем лист</h1>
        <p className="lead">
          {visit.patient.name}. Прошло {waited} с. Запись никуда не отправляется, всё считается на сервере клиники.
        </p>
        <ol className="steps">
          {STEPS.map((s, i) => (
            <li key={s.key} className={i < current ? 'done' : i === current ? 'now' : ''}>
              <span className="step-mark" aria-hidden="true">
                {i < current ? '✓' : i + 1}
              </span>
              <span>
                <b>{s.title}</b>
                <small>
                  {i === current && visit.step_detail ? visit.step_detail : s.hint}
                </small>
              </span>
            </li>
          ))}
        </ol>
      </div>
    )
  }

  return <Review visit={visit} onChange={setVisit} />
}
