import { useEffect, useState } from 'react'
import { api, clock, ruDate } from '../api'
import type { Doctor, HistoryRow, Stats } from '../api'

const STATUS: Record<string, { text: string; cls: string }> = {
  new: { text: 'не записан', cls: 'pill-muted' },
  processing: { text: 'обрабатывается', cls: 'pill-warn' },
  ready: { text: 'черновик', cls: 'pill-warn' },
  sent: { text: 'в МИС', cls: 'pill-ok' },
  error: { text: 'ошибка', cls: 'pill-danger' },
}

// Страница 8. Приёмы врача и сводные цифры.
export default function History({ doctor }: { doctor: Doctor }) {
  const [rows, setRows] = useState<HistoryRow[] | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    api.history(doctor.id).then(setRows).catch(() => setRows([]))
    api.stats().then(setStats).catch(() => {})
  }, [doctor.id])

  return (
    <div className="narrow wide-page">
      <h1>История приёмов</h1>
      {stats && stats.consultations > 0 && (
        <div className="stats">
          <div className="stat">
            <span>Приёмов обработано</span>
            <b>{stats.consultations}</b>
          </div>
          <div className="stat">
            <span>Полей принято без правок</span>
            <b>{stats.accepted_without_edits ?? '—'}%</b>
          </div>
          <div className="stat">
            <span>Средняя запись</span>
            <b>{stats.avg_audio_seconds ? clock(stats.avg_audio_seconds) : '—'}</b>
          </div>
          <div className="stat">
            <span>Средняя обработка</span>
            <b>{stats.avg_processing_seconds ? Math.round(stats.avg_processing_seconds) + ' с' : '—'}</b>
          </div>
        </div>
      )}
      {!rows && <p className="muted">Загружаем…</p>}
      {rows && rows.length === 0 && <p className="muted">Приёмов пока нет.</p>}
      {rows && rows.length > 0 && (
        <div className="card table-wrap">
          <table className="plain history">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Пациент</th>
                <th>Диагноз</th>
                <th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const s = STATUS[r.status] ?? STATUS.ready
                return (
                  <tr key={r.id}>
                    <td className="mono">
                      {ruDate(r.created_at)} {r.created_at.slice(11, 16)}
                    </td>
                    <td>
                      <a href={`#/visit/${r.id}`}>{r.patient.name || 'Пациент'}</a>
                    </td>
                    <td>
                      {r.icd && <span className="mono">{r.icd} </span>}
                      {r.diagnosis}
                    </td>
                    <td>
                      <span className={'pill ' + s.cls}>{s.text}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
