import { useEffect, useState } from 'react'
import { api, ruDate } from '../api'
import type { Doctor, Patient } from '../api'
import { go } from '../App'

const LANGS = [
  { id: 'ru', title: 'Русский', hint: 'приём идёт на русском' },
  { id: 'kk', title: 'Қазақша', hint: 'приём идёт на казахском' },
  { id: 'mixed', title: 'Смешанный', hint: 'оба языка вперемешку, обработка дольше' },
]

// Страница 2. Пациент, язык приёма и кнопка «Начать приём».
export default function NewVisit({ doctor }: { doctor: Doctor }) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<Patient[]>([])
  const [source, setSource] = useState('mis')
  const [patient, setPatient] = useState<Patient | null>(null)
  const [manual, setManual] = useState(false)
  // к акушеру-гинекологу записываются только пациентки: шаблон задаёт допустимый пол
  const needSex = doctor.patient_sex ?? null
  const blocked = (p: Patient) => !!needSex && !!p.sex && p.sex !== needSex
  const [form, setForm] = useState<Patient>({ name: '', iin: '', birth_date: '', sex: needSex ?? '' })
  const [language, setLanguage] = useState('mixed')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .patients(query)
        .then((r) => {
          setFound(r.items)
          setSource(r.source)
          if (r.source === 'offline') setManual(true)
        })
        .catch(() => setSource('offline'))
    }, 200)
    return () => clearTimeout(timer)
  }, [query])

  const manualOk = form.name.trim() && (!needSex || form.sex === needSex)
  const chosen = manual ? (manualOk ? form : null) : patient && !blocked(patient) ? patient : null

  const start = async () => {
    if (!chosen) return
    setBusy(true)
    setError('')
    try {
      const { id } = await api.create(doctor.id, chosen, language)
      go(`visit/${id}/record`)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="narrow">
      <h1>Новый приём</h1>

      <section className="card">
        <div className="card-head">
          <h2>Пациент</h2>
          <button className="link" onClick={() => setManual(!manual)}>
            {manual ? 'Найти в МИС' : 'Ввести вручную'}
          </button>
        </div>

        {!manual && (
          <>
            <label className="label" htmlFor="patient-search">
              Поиск в МИС по ФИО или ИИН
            </label>
            <input
              id="patient-search"
              className="input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Например, Нурланова"
              autoComplete="off"
            />
            {source === 'offline' && <p className="muted">МИС недоступна. Введите данные пациента вручную.</p>}
            <div className="patient-list">
              {found.map((p) => (
                <button
                  key={p.iin}
                  className={'patient' + (patient?.iin === p.iin ? ' on' : '')}
                  onClick={() => setPatient(p)}
                  disabled={blocked(p)}
                  title={blocked(p) ? `${doctor.specialty_title} принимает только пациенток` : undefined}
                >
                  <b>{p.name}</b>
                  {blocked(p) ? (
                    <span className="blocked">не для приёма у этого врача</span>
                  ) : (
                    <span className="mono">ИИН {p.iin}</span>
                  )}
                  <span>{ruDate(p.birth_date)}</span>
                </button>
              ))}
              {found.length === 0 && source === 'mis' && <p className="muted">Никого не нашли.</p>}
            </div>
          </>
        )}

        {manual && (
          <div className="form-grid">
            <div className="wide">
              <label className="label" htmlFor="p-name">
                ФИО
              </label>
              <input id="p-name" className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="label" htmlFor="p-iin">
                ИИН
              </label>
              <input
                id="p-iin"
                className="input mono"
                inputMode="numeric"
                maxLength={12}
                value={form.iin}
                onChange={(e) => setForm({ ...form, iin: e.target.value.replace(/\D/g, '') })}
              />
            </div>
            <div>
              <label className="label" htmlFor="p-sex">
                Пол
              </label>
              <select
                id="p-sex"
                className="input"
                value={form.sex ?? ''}
                onChange={(e) => setForm({ ...form, sex: e.target.value })}
              >
                <option value="">не указан</option>
                <option value="ж">женский</option>
                <option value="м">мужской</option>
              </select>
            </div>
            <div>
              <label className="label" htmlFor="p-birth">
                Дата рождения
              </label>
              <input
                id="p-birth"
                className="input"
                type="date"
                value={form.birth_date}
                onChange={(e) => setForm({ ...form, birth_date: e.target.value })}
              />
            </div>
          </div>
        )}
        {needSex && manual && form.sex !== needSex && (
          <div className="alert alert-warn">{doctor.specialty_title} принимает только пациенток.</div>
        )}
        {needSex && !manual && (
          <p className="note">{doctor.specialty_title} принимает только пациенток, остальные записи недоступны.</p>
        )}
        <p className="note">Данные пациента остаются в карте. Модель их не видит: в тексте разговора они заменяются метками.</p>
      </section>

      <section className="card">
        <h2>Язык приёма</h2>
        <div className="segmented" role="radiogroup" aria-label="Язык приёма">
          {LANGS.map((l) => (
            <button key={l.id} role="radio" aria-checked={language === l.id} className={language === l.id ? 'on' : ''} onClick={() => setLanguage(l.id)}>
              <b>{l.title}</b>
              <small>{l.hint}</small>
            </button>
          ))}
        </div>
      </section>

      {error && <div className="alert alert-danger">{error}</div>}
      <div className="actions">
        <button className="btn btn-primary btn-big" disabled={!chosen || busy} onClick={start}>
          {busy ? 'Открываем…' : 'Начать приём'}
        </button>
        <span className="muted">
          {chosen ? `${chosen.name}. Запись включится сразу.` : 'Сначала выберите пациента.'}
        </span>
      </div>
    </div>
  )
}
