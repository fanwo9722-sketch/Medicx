export default async function handler(req, res) {
    if (req.method !== 'POST') return res.status(405).end();
  
    const response = await fetch('https://serverless.roboflow.com/iiiii-srapi/workflows/detect-count-and-visualize-2', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body)
    });
  
    const data = await response.json();
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.status(200).json(data);
  }