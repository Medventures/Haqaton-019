import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Contour as ContourData } from '../api'

// Метки вида [ИИН_1] подсвечиваем, чтобы было видно, что ушло модели вместо данных.
function Masked({ text }: { text: string }) {
  const parts = text.split(/(\[[А-ЯЁ_]+_\d+\])/g)
  return (
    <>
      {parts.map((p, i) =>
        /^\[[А-ЯЁ_]+_\d+\]$/.test(p) ? (
          <span className="token mono" key={i}>
            {p}
          </span>
        ) : (
          p
        ),
      )}
    </>
  )
}

// Страница 6. Контур данных: что видела модель и куда уходили запросы.
export default function Contour({ id }: { id: number }) {
  const [data, setData] = useState<ContourData | null>(null)
  const [error, setError] = useState('')
  const [online, setOnline] = useState(navigator.onLine)

  useEffect(() => {
    api
      .contour(id)
      .then(setData)
      .catch((e: Error) => setError(e.message))
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [id])

  if (error) return <div className="narrow"><div className="alert alert-danger">{error}</div></div>
  if (!data) return <p className="muted narrow">Загружаем…</p>

  const changed = data.lines.filter((l) => l.text !== l.masked)
  const modelRequests = data.requests.filter((r) => r.model)

  return (
    <div className="contour">
      <a className="link" href={`#/visit/${id}`}>
        ‹ К листу консультации
      </a>
      <h1>Контур данных</h1>
      <p className="lead">Что видела модель и куда уходили запросы во время этого приёма.</p>

      <div className="stats">
        <div className={'stat ' + (data.external_requests === 0 ? 'good' : 'bad')}>
          <span>Запросов в интернет</span>
          <b>{data.external_requests}</b>
        </div>
        <div className="stat">
          <span>Звук приёма</span>
          <b>остался в клинике</b>
        </div>
        <div className="stat">
          <span>Замаскировано фрагментов</span>
          <b>{data.table.length}</b>
        </div>
        <div className={'stat ' + (data.leaks.length === 0 ? 'good' : 'bad')}>
          <span>Проверка перед моделью</span>
          <b>{data.leaks.length === 0 ? 'пройдена' : 'найдены данные'}</b>
        </div>
        <div className="stat">
          <span>Интернет на этом компьютере</span>
          <b>{online ? 'включён' : 'выключен'}</b>
        </div>
      </div>

      <div className="two">
        <section className="card">
          <h2 className="head-local">В карте приёма, на сервере клиники</h2>
          {changed.length === 0 && <p className="muted">В разговоре не было персональных данных.</p>}
          {changed.map((l) => (
            <p key={l.n} className="pair-line">
              <span className="mono muted">{l.n}</span> {l.text}
            </p>
          ))}
        </section>
        <section className="card">
          <h2 className="head-model">Это видела модель</h2>
          {changed.length === 0 && <p className="muted">Текст ушёл модели без изменений.</p>}
          {changed.map((l) => (
            <p key={l.n} className="pair-line">
              <span className="mono muted">{l.n}</span> <Masked text={l.masked} />
            </p>
          ))}
        </section>
      </div>

      <div className="two">
        <section className="card">
          <h2>Таблица замен</h2>
          <p className="muted small">Хранится только в базе клиники. Значения на экране прикрыты.</p>
          <table className="plain">
            <tbody>
              {data.table.map((r) => (
                <tr key={r.token}>
                  <td className="mono token-cell">{r.token}</td>
                  <td className="mono">{r.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <h2>Куда уходили запросы</h2>
          <table className="plain">
            <tbody>
              <tr>
                <td>Распознавание речи</td>
                <td className="mono">whisper, этот сервер</td>
                <td>
                  <span className="pill pill-ok">внутри клиники</span>
                </td>
              </tr>
              <tr>
                <td>
                  Модель, {modelRequests.length} запр.
                  <br />
                  <span className="muted small">{data.model.model}</span>
                </td>
                <td className="mono">{data.model.url}</td>
                <td>
                  <span className={'pill ' + (data.model.local ? 'pill-ok' : 'pill-danger')}>
                    {data.model.local ? 'внутри клиники' : 'внешний адрес'}
                  </span>
                </td>
              </tr>
              <tr>
                <td>
                  МИС
                  <br />
                  <span className="muted small">{data.mis.sent_at ? 'лист передан' : 'лист ещё не передан'}</span>
                </td>
                <td className="mono">{data.mis.url}</td>
                <td>
                  <span className={'pill ' + (data.mis.local ? 'pill-ok' : 'pill-warn')}>
                    {data.mis.local ? 'внутри клиники' : 'внешний адрес'}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <p className="note">
            Проверить просто: выключите Wi-Fi и запишите новый приём. Распознавание и заполнение листа продолжат работать.
          </p>
        </section>
      </div>
    </div>
  )
}
