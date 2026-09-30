import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Doctor } from '../api'

// Страница 1. Врач выбирает себя: по специальности подставится свой шаблон листа.
export default function Doctors({ current, onPick }: { current: Doctor | null; onPick: (d: Doctor) => void }) {
  const [doctors, setDoctors] = useState<Doctor[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .doctors()
      .then(setDoctors)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <div className="narrow">
      <h1>Кто ведёт приём</h1>
      <p className="lead">У каждой специальности свой лист консультации и свой словарь терминов.</p>
      {error && <div className="alert alert-danger">{error}</div>}
      {!doctors && !error && <p className="muted">Загружаем список врачей…</p>}
      <div className="doctor-grid">
        {doctors?.map((d) => (
          <button key={d.id} className={'doctor' + (current?.id === d.id ? ' on' : '')} onClick={() => onPick(d)}>
            <span className="avatar big" aria-hidden="true">
              {d.name
                .split(' ')
                .slice(0, 2)
                .map((w) => w[0])
                .join('')}
            </span>
            <span className="doctor-text">
              <b>{d.name}</b>
              <span>{d.position}</span>
            </span>
            <span className="tag">{d.specialty_title}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
