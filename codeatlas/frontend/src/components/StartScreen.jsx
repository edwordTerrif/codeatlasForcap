const SAMPLES = [
  { label: 'psf/requests', url: 'https://github.com/psf/requests', note: 'HTTP 라이브러리, 파일 20여 개' },
  { label: 'pallets/flask', url: 'https://github.com/pallets/flask', note: '웹 프레임워크, 파일 20여 개' },
  { label: 'pallets/click', url: 'https://github.com/pallets/click', note: '명령줄 도구 라이브러리' },
]

export default function StartScreen({ projects, onSample, onOpen }) {
  return (
    <div className="start">
      <h1>처음 보는 코드를 지도로 펼쳐 보세요</h1>
      <p className="lead">
        GitHub 저장소 주소를 넣으면 폴더, 파일, 함수가 서로 어떻게 이어지는지 지도로 그려요.
        궁금한 걸 물어보면 답과 함께 관련 코드의 호출 경로를 지도 위에 표시해요.
      </p>
      <div className="samples">
        <span>예제로 시작하기</span>
        {SAMPLES.map((s) => (
          <button key={s.url} type="button" className="chip" onClick={() => onSample(s.url)} title={s.note}>
            {s.label}
          </button>
        ))}
      </div>
      {projects.length > 0 && (
        <section className="recent">
          <h2>이전에 그린 지도</h2>
          <ul>
            {projects.map((p) => (
              <li key={p.id}>
                <button type="button" onClick={() => onOpen(p.id)}>
                  <code>{p.name}</code>
                  <span>
                    파일 {p.stats?.files ?? '?'}개, 함수 {p.stats?.functions ?? '?'}개
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
