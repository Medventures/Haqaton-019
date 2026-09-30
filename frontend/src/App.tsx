import { useEffect, useState } from 'react'
import { api } from './api'
import type { Doctor } from './api'
import Contour from './pages/Contour'
import Doctors from './pages/Doctors'
import History from './pages/History'
import NewVisit from './pages/NewVisit'
import Record from './pages/Record'
import Visit from './pages/Visit'

// Маршруты живут в адресе после #: так приложение работает с любого адреса
// сервера клиники без настройки.
function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  const [parts, setParts] = useState<string[]>(read)
  useEffect(() => {
    const onChange = () => setParts(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return parts
}

export const go = (path: string) => {
  window.location.hash = '#/' + path
}

const DOCTOR_KEY = 'hatshy.doctor'

function loadDoctor(): Doctor | null {
  try {
    const raw = localStorage.getItem(DOCTOR_KEY)
    return raw ? (JSON.parse(raw) as Doctor) : null
  } catch {
    return null
  }
}

export default function App() {
  const route = useRoute()
  const [doctor, setDoctor] = useState<Doctor | null>(loadDoctor)
  const [modelOk, setModelOk] = useState<boolean | null>(null)

  useEffect(() => {
    api
      .health()
      .then((h) => setModelOk(h.model.ok))
      .catch(() => setModelOk(false))
  }, [])

  const choose = (d: Doctor | null) => {
    setDoctor(d)
    try {
      if (d) localStorage.setItem(DOCTOR_KEY, JSON.stringify(d))
      else localStorage.removeItem(DOCTOR_KEY)
    } catch {
      // хранилище недоступно: выбор врача живёт до перезагрузки страницы
    }
  }

  let page
  if (route[0] === 'visit' && route[1]) {
    // приём открывается и по прямой ссылке: врач записан в самом приёме
    const id = Number(route[1])
    if (route[2] === 'record') page = <Record id={id} key={id} />
    else if (route[2] === 'contour') page = <Contour id={id} key={id} />
    else page = <Visit id={id} key={id} />
  } else if (!doctor || route.length === 0) {
    page = (
      <Doctors
        current={doctor}
        onPick={(d) => {
          choose(d)
          go('new')
        }}
      />
    )
  } else if (route[0] === 'new') {
    page = <NewVisit doctor={doctor} />
  } else if (route[0] === 'history') {
    page = <History doctor={doctor} />
  } else {
    page = <Doctors current={doctor} onPick={(d) => (choose(d), go('new'))} />
  }

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5 11a7 7 0 0 0 14 0" />
              <path d="M12 18v3" />
            </svg>
          </span>
          Хатшы
        </a>
        {doctor && (
          <nav className="nav">
            <a href="#/new" className={route[0] === 'new' ? 'on' : ''}>
              Новый приём
            </a>
            <a href="#/history" className={route[0] === 'history' ? 'on' : ''}>
              История приёмов
            </a>
          </nav>
        )}
        <div className="grow" />
        <span className={'pill ' + (modelOk === false ? 'pill-danger' : 'pill-ok')}>
          <span className="dot" />
          {modelOk === false ? 'Локальная модель недоступна' : 'Всё работает на сервере клиники'}
        </span>
        {doctor && (
          <a className="who" href="#/" title="Сменить врача">
            <span className="avatar" aria-hidden="true">
              {doctor.name
                .split(' ')
                .slice(0, 2)
                .map((w) => w[0])
                .join('')}
            </span>
            <span>
              <b>{doctor.name}</b>
              <small>{doctor.specialty_title}</small>
            </span>
          </a>
        )}
      </header>
      <main className="page">{page}</main>
    </div>
  )
}
