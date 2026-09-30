// Обращения к серверу Хатшы. Сервер стоит в клинике, адрес относительный.

export type Doctor = {
  id: number
  name: string
  position: string
  specialty: string
  specialty_title: string
  patient_sex?: string | null
}

export type Patient = {
  name: string
  iin: string
  birth_date?: string
  sex?: string
  allergies?: string[]
  note?: string
}

export type Quote = { n: number; start: number; text: string }
export type Flag = { level: 'warn' | 'danger' | 'info'; text: string; drug?: string }
export type Vital = { key: string; title: string; unit: string; value: string; note?: string | null }
export type Drug = { drug: string; dose: string; frequency: string; duration: string; quotes?: Quote[] }
export type IcdOption = { code: string; name: string; score?: number }

export type Field = {
  title: string
  kind: 'text' | 'vitals' | 'prescriptions' | 'diagnosis' | 'date_future' | 'date_past'
  required: boolean
  value: string
  status: 'ok' | 'check' | 'empty' | 'danger'
  flags: Flag[]
  quotes: Quote[]
  edited: boolean
  confirmed?: boolean
  items?: (Vital | Drug)[]
  icd?: { code: string; name: string; options: IcdOption[]; sure: boolean; source?: string }
  raw?: string
  date?: string | null
  rule?: string
  pregnancy?: { weeks: number; days: number; edd: string; rule: string }
}

export type Segment = {
  n: number
  start: number
  end: number
  text: string
  role: 'doctor' | 'patient'
  lang: string
  heard?: string
  fixes?: { from: string; to: string }[]
  ru?: string
  untranslated?: boolean
}

export type Consultation = {
  id: number
  status: 'new' | 'processing' | 'ready' | 'sent' | 'error'
  step: string | null
  step_detail: string | null
  error: string | null
  patient: Patient
  doctor: Doctor
  language: string
  template: string
  created_at: string
  visit_date: string
  segments: Segment[] | null
  fields: Record<string, Field> | null
  timings: { asr: number; fields: number; total: number; audio: number; translate?: number } | null
  gaps: { from: number; to: number }[] | null
  blocking: (Flag & { field: string })[]
  sent_at: string | null
  mis_id: string | null
  has_audio: boolean
}

export type Template = {
  id: string
  title: string
  sheet_title: string
  fields: { id: string; title: string; kind: string }[]
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, init)
  if (!res.ok) {
    let message = `Ошибка сервера (${res.status})`
    try {
      const body = await res.json()
      if (body.detail) message = String(body.detail)
    } catch {
      // ответ без тела
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => call<{ model: { ok: boolean; model: string; local: boolean }; mis_local: boolean }>('/health'),
  doctors: () => call<Doctor[]>('/doctors'),
  template: (id: string) => call<Template>('/templates/' + id),
  patients: (q: string) =>
    call<{ source: string; items: Patient[] }>('/patients?q=' + encodeURIComponent(q)),
  icd: (q: string) => call<IcdOption[]>('/icd?q=' + encodeURIComponent(q)),
  create: (doctor_id: number, patient: Patient, language: string) =>
    call<{ id: number }>('/consultations', json({ doctor_id, patient, language })),
  get: (id: number) => call<Consultation>('/consultations/' + id),
  history: (doctorId: number) => call<HistoryRow[]>('/consultations?doctor_id=' + doctorId),
  upload: (id: number, blob: Blob, filename: string) => {
    const form = new FormData()
    form.append('file', blob, filename)
    return call<{ status: string }>(`/consultations/${id}/audio`, { method: 'POST', body: form })
  },
  dictate: (id: number, field: string, blob: Blob, filename: string) => {
    const form = new FormData()
    form.append('file', blob, filename)
    return call<{ text: string; items?: (Vital | Drug)[] }>(`/consultations/${id}/dictate/${field}`, {
      method: 'POST',
      body: form,
    })
  },
  reprocess: (id: number) => call<{ status: string }>(`/consultations/${id}/reprocess`, { method: 'POST' }),
  editField: (id: number, field: string, patch: { value?: string; items?: unknown[]; icd?: IcdOption }) =>
    call<Consultation>(`/consultations/${id}/fields/${field}`, json(patch, 'PATCH')),
  confirmField: (id: number, field: string, note = '') =>
    call<Consultation>(`/consultations/${id}/fields/${field}/confirm`, json({ note })),
  setRole: (id: number, n: number, role: string) =>
    call<{ ok: boolean }>(`/consultations/${id}/segments/${n}`, json({ role }, 'PATCH')),
  protocol: (id: number) => call<Protocol>(`/consultations/${id}/protocol`),
  protocolAdd: (id: number, section: string, text: string) =>
    call<Consultation>(`/consultations/${id}/protocol/add`, json({ section, text })),
  contour: (id: number) => call<Contour>(`/consultations/${id}/contour`),
  send: (id: number) => call<Consultation>(`/consultations/${id}/send`, { method: 'POST' }),
  stats: () => call<Stats>('/stats'),
}

export type HistoryRow = {
  id: number
  patient: Patient
  status: string
  created_at: string
  sent_at: string | null
  diagnosis: string
  icd: string
  timings: { total: number; audio: number } | null
}

export type Protocol = {
  found: boolean
  icd: string
  title?: string
  version?: string
  approved?: string
  source?: string
  verified_by_doctor?: boolean
  need?: string
  visit?: { title: string; exact: boolean; weeks: number; days: number }
  sections?: { key: string; title: string; items: { text: string; note: string; present: boolean }[] }[]
  available?: { title: string; icd: string[] }[]
}

export type Contour = {
  lines: { n: number; role: string; text: string; masked: string }[]
  table: { token: string; kind: string; value: string }[]
  leaks: string[]
  requests: { to: string; local: boolean; purpose: string; model?: string; chars: number; seconds: number }[]
  external_requests: number
  model: { ok: boolean; model: string; url: string; local: boolean }
  storage: { database: string; audio: string }
  mis: { url: string; local: boolean; sent_at: string | null }
}

export type Stats = {
  consultations: number
  fields_total: number
  fields_filled: number
  fields_edited: number
  accepted_without_edits: number | null
  avg_processing_seconds: number | null
  avg_audio_seconds: number | null
}

export const clock = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export const ruDate = (iso?: string | null) => {
  if (!iso) return ''
  const [y, m, d] = iso.slice(0, 10).split('-')
  return `${d}.${m}.${y}`
}
