import { useEffect, useRef, useState } from 'react'
import { api, clock } from '../api'
import type { Consultation } from '../api'
import { go } from '../App'

type Phase = 'starting' | 'recording' | 'paused' | 'uploading' | 'denied'

const BARS = 40

// Страница 3. Запись приёма. Звук уходит только на сервер клиники.
export default function Record({ id }: { id: number }) {
  const [visit, setVisit] = useState<Consultation | null>(null)
  const [phase, setPhase] = useState<Phase>('starting')
  const [seconds, setSeconds] = useState(0)
  const [levels, setLevels] = useState<number[]>(() => Array(BARS).fill(0))
  const [error, setError] = useState('')

  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const stream = useRef<MediaStream | null>(null)
  const audioCtx = useRef<AudioContext | null>(null)
  const phaseRef = useRef<Phase>('starting')
  phaseRef.current = phase

  useEffect(() => {
    api
      .get(id)
      .then(setVisit)
      .catch((e: Error) => setError(e.message))
  }, [id])

  useEffect(() => {
    let cancelled = false
    let raf = 0
    let timer = 0

    const begin = async () => {
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop())
          return
        }
        stream.current = s
        const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find((m) => MediaRecorder.isTypeSupported(m))
        const rec = new MediaRecorder(s, mime ? { mimeType: mime } : undefined)
        rec.ondataavailable = (e) => e.data.size && chunks.current.push(e.data)
        rec.start(1000)
        recorder.current = rec
        setPhase('recording')

        // индикатор громкости: врач видит, что микрофон слышит разговор
        const ctx = new AudioContext()
        audioCtx.current = ctx
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 512
        ctx.createMediaStreamSource(s).connect(analyser)
        const buf = new Uint8Array(analyser.fftSize)
        let last = 0
        const tick = (now: number) => {
          raf = requestAnimationFrame(tick)
          if (now - last < 90) return
          last = now
          analyser.getByteTimeDomainData(buf)
          let sum = 0
          for (const v of buf) sum += (v - 128) * (v - 128)
          const level = phaseRef.current === 'recording' ? Math.min(1, Math.sqrt(sum / buf.length) / 40) : 0
          setLevels((prev) => [...prev.slice(1), level])
        }
        raf = requestAnimationFrame(tick)
        timer = window.setInterval(() => {
          if (phaseRef.current === 'recording') setSeconds((x) => x + 1)
        }, 1000)
      } catch {
        if (!cancelled) setPhase('denied')
      }
    }
    begin()

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      clearInterval(timer)
      if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
      stream.current?.getTracks().forEach((t) => t.stop())
      audioCtx.current?.close().catch(() => {})
    }
  }, [])

  const togglePause = () => {
    const rec = recorder.current
    if (!rec) return
    if (rec.state === 'recording') {
      rec.pause()
      setPhase('paused')
    } else if (rec.state === 'paused') {
      rec.resume()
      setPhase('recording')
    }
  }

  const send = async (blob: Blob, filename: string) => {
    setPhase('uploading')
    setError('')
    try {
      await api.upload(id, blob, filename)
      go(`visit/${id}`)
    } catch (e) {
      setError((e as Error).message)
      setPhase('paused')
    }
  }

  const finish = () => {
    const rec = recorder.current
    if (!rec || rec.state === 'inactive') return
    rec.onstop = () => {
      const type = rec.mimeType || 'audio/webm'
      const blob = new Blob(chunks.current, { type })
      stream.current?.getTracks().forEach((t) => t.stop())
      send(blob, type.includes('mp4') ? 'visit.m4a' : 'visit.webm')
    }
    rec.stop()
  }

  const pickFile = (file: File | undefined) => {
    if (!file) return
    if (recorder.current && recorder.current.state !== 'inactive') {
      recorder.current.onstop = null
      recorder.current.stop()
    }
    stream.current?.getTracks().forEach((t) => t.stop())
    send(file, file.name)
  }

  return (
    <div className="record">
      <section className="card record-main">
        {phase === 'denied' ? (
          <>
            <h1>Нет доступа к микрофону</h1>
            <p className="lead">Разрешите доступ к микрофону в браузере и обновите страницу. Либо загрузите готовую запись.</p>
          </>
        ) : (
          <>
            <span className={'pill ' + (phase === 'recording' ? 'pill-danger' : 'pill-muted')}>
              <span className="dot" />
              {phase === 'starting' && 'Включаем микрофон…'}
              {phase === 'recording' && 'Идёт запись приёма'}
              {phase === 'paused' && 'Пауза'}
              {phase === 'uploading' && 'Отправляем запись на сервер клиники…'}
            </span>
            <div className="timer" aria-live="off">
              {clock(seconds)}
            </div>
            <div className="bars" aria-hidden="true">
              {levels.map((v, i) => (
                <span key={i} style={{ height: `${6 + v * 66}px`, opacity: v > 0.04 ? 1 : 0.35 }} />
              ))}
            </div>
            <div className="actions center">
              <button className="btn btn-big" onClick={togglePause} disabled={phase === 'starting' || phase === 'uploading'}>
                {phase === 'paused' ? 'Продолжить' : 'Пауза'}
              </button>
              <button className="btn btn-primary btn-big" onClick={finish} disabled={phase === 'starting' || phase === 'uploading' || seconds < 2}>
                Завершить приём
              </button>
            </div>
          </>
        )}
        {error && <div className="alert alert-danger">{error}</div>}
        <label className="link file-link">
          Загрузить готовую запись
          <input type="file" accept="audio/*,video/webm,.wav,.mp3,.m4a,.webm,.ogg" onChange={(e) => pickFile(e.target.files?.[0])} hidden />
        </label>
      </section>

      <aside className="card record-side">
        <h2>Приём</h2>
        {visit && (
          <dl className="facts">
            <dt>Пациент</dt>
            <dd>{visit.patient.name}</dd>
            <dt>Врач</dt>
            <dd>{visit.doctor.name}</dd>
            <dt>Лист</dt>
            <dd>{visit.doctor.specialty_title}</dd>
            <dt>Язык</dt>
            <dd>{{ ru: 'русский', kk: 'казахский', mixed: 'смешанный' }[visit.language] ?? visit.language}</dd>
          </dl>
        )}
        <p className="note">
          Ведите приём как обычно. После кнопки «Завершить приём» запись распознаётся на сервере клиники, и лист заполняется сам.
        </p>
      </aside>
    </div>
  )
}
